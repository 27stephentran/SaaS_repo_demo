"""Omnichannel Webhook Endpoints for Meta Graph API (v18.0+) and SePay VietQR IPN."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import erp_repository, tenant_db_session
from app.models.entities import (
    CustomerResponse,
    EmployeeResponse,
    OrderCreateRequest,
    OrderLineItemRequest,
    OrderResponse,
    OrderStatus,
    SePayIPNWebhookPayload,
)
from app.services.ai_rag import sales_rag_engine
from app.services.integrations import (
    meta_graph_client,
    sepay_client,
    telegram_dispatch_client,
)
from app.services.order_service import order_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])


def _is_production_env() -> bool:
    """Return True when running in production mode where unregistered channels fail closed."""
    settings = get_settings()
    env_val = os.getenv("APP_ENV", settings.app_env)
    return str(env_val).strip().lower() == "production"


async def _get_or_create_meta_customer(
    sender_psid: str,
    tenant_id: str,
) -> CustomerResponse:
    """Look up or create a tenant-scoped Meta PSID customer in PostgreSQL or local fallback."""
    if await erp_repository.is_postgres_active():
        async with tenant_db_session(tenant_id) as session:
            stmt = text(
                """
                INSERT INTO customers (tenant_id, meta_psid, full_name)
                VALUES (:tenant_id, :meta_psid, :full_name)
                ON CONFLICT (tenant_id, meta_psid) DO UPDATE
                    SET full_name = customers.full_name
                RETURNING id, tenant_id, meta_psid, full_name, phone, created_at
                """
            )
            result = await session.execute(
                stmt,
                {
                    "tenant_id": tenant_id,
                    "meta_psid": sender_psid,
                    "full_name": "Meta Shopper",
                },
            )
            row = dict(result.mappings().one())
            return CustomerResponse.model_validate(row)

    return erp_repository.get_or_create_customer_by_psid(
        meta_psid=sender_psid,
        tenant_id=tenant_id,
    )


async def _fetch_order_by_code(order_code: str, tenant_id: str) -> OrderResponse | None:
    """Fetch a tenant-scoped order by order_code from PostgreSQL or local fallback."""
    if await erp_repository.is_postgres_active():
        async with tenant_db_session(tenant_id) as session:
            stmt = text(
                """
                SELECT
                    o.id,
                    o.tenant_id,
                    o.order_code,
                    o.customer_id,
                    COALESCE(c.full_name, 'Enterprise Customer') AS customer_name,
                    o.assigned_employee_id,
                    o.status,
                    o.total_amount_vnd,
                    o.sepay_qr_url,
                    o.sepay_transaction_id,
                    o.items_json,
                    o.created_at,
                    o.updated_at
                FROM orders o
                LEFT JOIN customers c ON c.id = o.customer_id AND c.tenant_id = o.tenant_id
                WHERE UPPER(o.order_code) = :order_code
                  AND o.tenant_id = :tenant_id
                LIMIT 1
                """
            )
            result = await session.execute(
                stmt,
                {
                    "order_code": order_code.upper().strip(),
                    "tenant_id": tenant_id,
                },
            )
            row = result.mappings().first()
            return OrderResponse.model_validate(dict(row)) if row else None

    return erp_repository.get_order_by_code(order_code=order_code, tenant_id=tenant_id)


async def _fetch_sepay_webhook_log(
    transaction_id: str,
    tenant_id: str,
) -> dict[str, Any] | None:
    """Fetch an existing SePay webhook log entry from PostgreSQL or local fallback."""
    if await erp_repository.is_postgres_active():
        async with tenant_db_session(tenant_id) as session:
            stmt = text(
                """
                SELECT sepay_transaction_id, tenant_id, order_code, processing_status
                FROM sepay_webhook_logs
                WHERE sepay_transaction_id = :tx_id
                LIMIT 1
                """
            )
            result = await session.execute(stmt, {"tx_id": transaction_id})
            row = result.mappings().first()
            return dict(row) if row else None

    return erp_repository.get_sepay_webhook_log(transaction_id)


async def _persist_sepay_event(
    *,
    transaction_id: str,
    tenant_id: str,
    order_code: str | None,
    payload: SePayIPNWebhookPayload,
    raw_payload_dict: dict[str, Any],
    processing_status: str,
    mark_paid: bool = False,
) -> tuple[bool, OrderResponse | None]:
    """Atomically persist SePay webhook log and optional PAID transition in DB or local fallback."""
    if await erp_repository.is_postgres_active():
        inserted = await erp_repository.persist_sepay_webhook_to_db(
            transaction_id=transaction_id,
            order_code=order_code,
            transfer_type=payload.transferType,
            transfer_amount=payload.transferAmount,
            account_number=payload.accountNumber,
            sub_account=payload.subAccount,
            reference_code=payload.referenceCode,
            processing_status=processing_status,
            raw_payload=raw_payload_dict,
            mark_paid=mark_paid,
            tenant_id=tenant_id,
        )
        sepay_client.mark_transaction_processed(transaction_id)
        if not inserted:
            return False, None
        updated_order = (
            await _fetch_order_by_code(order_code=order_code, tenant_id=tenant_id)
            if mark_paid and order_code
            else None
        )
        return True, updated_order

    inserted = erp_repository.record_sepay_webhook_log(
        transaction_id=transaction_id,
        order_code=order_code,
        transfer_type=payload.transferType,
        transfer_amount=payload.transferAmount,
        processing_status=processing_status,
        raw_payload=raw_payload_dict,
        tenant_id=tenant_id,
        account_number=payload.accountNumber,
        sub_account=payload.subAccount,
        reference_code=payload.referenceCode,
    )
    sepay_client.mark_transaction_processed(transaction_id)
    if not inserted:
        return False, None

    updated_order: OrderResponse | None = None
    if mark_paid and order_code:
        updated_order = erp_repository.mark_order_paid_by_code(
            order_code=order_code,
            transaction_id=transaction_id,
            tenant_id=tenant_id,
        )
    return True, updated_order


async def _select_target_employees(
    assigned_employee_id: UUID | None,
    tenant_id: str,
) -> list[EmployeeResponse]:
    """Select active tenant fulfillment employee(s) for Telegram dispatch."""
    employees = await order_service.list_employees(tenant_id=tenant_id)
    active_employees = [emp for emp in employees if emp.is_active]
    if not active_employees:
        return []
    if assigned_employee_id is not None:
        assigned = next(
            (emp for emp in active_employees if emp.id == assigned_employee_id),
            None,
        )
        if assigned is not None:
            return [assigned]
    return active_employees


async def _dispatch_sepay_telegram_alerts(
    *,
    dispatches: list[tuple[UUID, str]],
    order_code: str,
    total_amount_vnd: int,
    items_summary: str,
    tenant_id: str,
) -> None:
    """Execute queued Telegram fulfillment alerts and transition outbox jobs."""
    for job_id, telegram_user_id in dispatches:
        try:
            result = await telegram_dispatch_client.dispatch_order_alert(
                telegram_user_id=telegram_user_id,
                order_code=order_code,
                total_amount_vnd=total_amount_vnd,
                items_summary=items_summary,
            )
            if isinstance(result, dict) and result.get("dispatched") is False:
                status_code = result.get("status_code")
                error_type = result.get("error_type") or "TelegramError"
                await erp_repository.mark_outbox_job_failed(
                    job_id,
                    error_message=(
                        f"Telegram Bot API dispatch failed ({error_type}, status_code={status_code})"
                    ),
                    tenant_id=tenant_id,
                )
            else:
                await erp_repository.mark_outbox_job_completed(
                    job_id,
                    tenant_id=tenant_id,
                )
        except Exception as exc:
            logger.warning(
                "Background Telegram dispatch failed (job_id=%s, order_code=%s, error_type=%s)",
                job_id,
                order_code,
                type(exc).__name__,
            )
            await erp_repository.mark_outbox_job_failed(
                job_id,
                error_message=f"Telegram Bot API dispatch failed ({type(exc).__name__})",
                tenant_id=tenant_id,
            )


@router.get("/meta", response_class=PlainTextResponse)
async def verify_meta_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
) -> PlainTextResponse:
    """Verify Meta Graph API v18.0+ webhook subscription challenge."""
    settings = get_settings()
    if (
        settings.meta_verify_token
        and hub_mode == "subscribe"
        and hmac.compare_digest(hub_verify_token, settings.meta_verify_token)
    ):
        return PlainTextResponse(content=hub_challenge, status_code=status.HTTP_200_OK)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid Meta webhook verification token",
    )


@router.post("/meta")
async def handle_meta_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """Process inbound Meta Messenger/Instagram DMs and public comments via Gemini RAG."""
    raw_body = await request.body()
    if not meta_graph_client.verify_webhook_signature(
        raw_body=raw_body,
        signature_header=x_hub_signature_256,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Meta webhook signature",
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON webhook payload",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Meta webhook payload must be a JSON object",
        )

    settings = get_settings()
    is_prod = _is_production_env()
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        entries = []
    processed_events: list[dict[str, Any]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        page_id = str(entry.get("id") or "").strip()
        resolved_tenant = (
            await erp_repository.resolve_channel_tenant("meta_page", page_id)
            if page_id
            else None
        )
        if resolved_tenant is None:
            if is_prod:
                processed_events.append(
                    {
                        "type": "entry",
                        "page_id": page_id,
                        "status": "skipped_unregistered_channel",
                    }
                )
                continue
            tenant_id = settings.default_tenant_id
        else:
            tenant_id = resolved_tenant

        # 1. Handle direct Messenger / Instagram messages
        for messaging_event in entry.get("messaging", []):
            if not isinstance(messaging_event, dict):
                continue
            sender_psid = str(messaging_event.get("sender", {}).get("id") or "").strip()
            message_obj = messaging_event.get("message", {})
            if not isinstance(message_obj, dict):
                continue
            message_text = str(message_obj.get("text") or "")
            if not sender_psid or not message_text:
                continue

            mid = str(message_obj.get("mid") or "").strip()
            external_event_id = mid or f"msg:{payload_hash}:{sender_psid}"
            is_new = await erp_repository.record_webhook_dedup(
                provider="meta",
                external_event_id=external_event_id,
                payload_hash=payload_hash,
                tenant_id=tenant_id,
            )
            if not is_new:
                processed_events.append(
                    {
                        "type": "messaging",
                        "tenant_id": tenant_id,
                        "sender_psid": sender_psid,
                        "mid": external_event_id,
                        "status": "skipped_duplicate",
                    }
                )
                continue

            customer = await _get_or_create_meta_customer(
                sender_psid=sender_psid,
                tenant_id=tenant_id,
            )
            query_embedding = await sales_rag_engine.embed_text(message_text)
            matched_items = await order_service.search_inventory(
                query_embedding=query_embedding,
                query_text=message_text,
                tenant_id=tenant_id,
                limit=3,
            )
            rag_result = await sales_rag_engine.generate_sales_reply(
                customer_message=message_text,
                matched_items=matched_items,
                tenant_id=tenant_id,
            )

            created_order_code: str | None = None
            sepay_qr_url: str | None = None

            if rag_result["order_intent_detected"] and matched_items:
                recommended_sku = rag_result.get("recommended_sku")
                selected_item = next(
                    (item for item in matched_items if item.sku == recommended_sku),
                    matched_items[0],
                )
                recommended_qty = max(1, int(rag_result.get("recommended_quantity") or 1))
                try:
                    created_order = await order_service.create_order(
                        payload=OrderCreateRequest(
                            tenant_id=tenant_id,
                            customer_id=customer.id,
                            items=[
                                OrderLineItemRequest(
                                    sku=selected_item.sku,
                                    quantity=recommended_qty,
                                )
                            ],
                        ),
                        tenant_id=tenant_id,
                    )
                    created_order_code = created_order.order_code
                    sepay_qr_url = created_order.sepay_qr_url
                    rag_result["reply_text"] += (
                        f"\n\n💳 Mã đơn hàng: {created_order_code} — Quét VietQR để thanh toán: {sepay_qr_url}"
                    )
                except HTTPException as exc:
                    logger.warning(
                        "Meta webhook order creation failed for tenant=%s sku=%s: %s",
                        tenant_id,
                        selected_item.sku,
                        exc.detail,
                    )
                    if exc.status_code == status.HTTP_409_CONFLICT:
                        rag_result["reply_text"] += (
                            "\n\n⚠️ Hiện sản phẩm đang tạm hết hàng, shop sẽ kiểm tra và phản hồi anh/chị sớm nhất ạ."
                        )

            outbox_job_id = await erp_repository.enqueue_outbox_job(
                job_type="meta_send_message",
                payload={
                    "recipient_psid": sender_psid,
                    "text": rag_result["reply_text"],
                    "order_code": created_order_code,
                    "mid": external_event_id,
                },
                tenant_id=tenant_id,
            )
            try:
                delivery = await meta_graph_client.send_message(
                    recipient_psid=sender_psid,
                    text=rag_result["reply_text"],
                )
                if delivery.get("delivered") is False or "error" in delivery:
                    status_code = delivery.get("status_code")
                    await erp_repository.mark_outbox_job_failed(
                        outbox_job_id,
                        error_message=f"Meta Graph API delivery failed (status_code={status_code})",
                        tenant_id=tenant_id,
                    )
                    delivery = {
                        "delivered": False,
                        "queued": True,
                        "outbox_job_id": str(outbox_job_id),
                        "error": "Meta Graph API delivery failed",
                        "status_code": status_code,
                    }
                else:
                    await erp_repository.mark_outbox_job_completed(
                        outbox_job_id,
                        tenant_id=tenant_id,
                    )
            except Exception as exc:
                status_code = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                logger.warning(
                    "Meta Graph API delivery failed (outbox_job_id=%s, error_type=%s, status_code=%s)",
                    outbox_job_id,
                    type(exc).__name__,
                    status_code,
                )
                await erp_repository.mark_outbox_job_failed(
                    outbox_job_id,
                    error_message=(
                        f"Meta Graph API delivery failed ({type(exc).__name__}, status_code={status_code})"
                    ),
                    tenant_id=tenant_id,
                )
                delivery = {
                    "delivered": False,
                    "queued": True,
                    "outbox_job_id": str(outbox_job_id),
                    "error": "Meta Graph API delivery failed",
                    "error_type": type(exc).__name__,
                    "status_code": status_code,
                }

            processed_events.append(
                {
                    "type": "messaging",
                    "tenant_id": tenant_id,
                    "mid": external_event_id,
                    "sender_psid": sender_psid,
                    "reply_text": rag_result["reply_text"],
                    "model_used": rag_result["model_used"],
                    "order_intent_detected": rag_result["order_intent_detected"],
                    "order_code": created_order_code,
                    "sepay_qr_url": sepay_qr_url,
                    "outbox_job_id": str(outbox_job_id),
                    "meta_delivery": delivery,
                }
            )

        # 2. Handle public post comment events via Private Replies API
        for change in entry.get("changes", []):
            if not isinstance(change, dict):
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            if change.get("field") == "feed" and value.get("item") == "comment":
                comment_id = str(value.get("comment_id") or "").strip()
                comment_text = str(value.get("message") or "")
                if not comment_id or not comment_text:
                    continue

                is_new = await erp_repository.record_webhook_dedup(
                    provider="meta",
                    external_event_id=comment_id,
                    payload_hash=payload_hash,
                    tenant_id=tenant_id,
                )
                if not is_new:
                    processed_events.append(
                        {
                            "type": "private_reply",
                            "tenant_id": tenant_id,
                            "comment_id": comment_id,
                            "status": "skipped_duplicate",
                        }
                    )
                    continue

                query_embedding = await sales_rag_engine.embed_text(comment_text)
                matched_items = await order_service.search_inventory(
                    query_embedding=query_embedding,
                    query_text=comment_text,
                    tenant_id=tenant_id,
                    limit=2,
                )
                rag_result = await sales_rag_engine.generate_sales_reply(
                    customer_message=comment_text,
                    matched_items=matched_items,
                    tenant_id=tenant_id,
                )
                outbox_job_id = await erp_repository.enqueue_outbox_job(
                    job_type="meta_private_reply",
                    payload={
                        "comment_id": comment_id,
                        "text": rag_result["reply_text"],
                    },
                    tenant_id=tenant_id,
                )
                try:
                    delivery = await meta_graph_client.send_private_reply(
                        comment_id=comment_id,
                        text=rag_result["reply_text"],
                    )
                    if delivery.get("delivered") is False or "error" in delivery:
                        status_code = delivery.get("status_code")
                        await erp_repository.mark_outbox_job_failed(
                            outbox_job_id,
                            error_message=(
                                f"Meta Graph API delivery failed (status_code={status_code})"
                            ),
                            tenant_id=tenant_id,
                        )
                        delivery = {
                            "delivered": False,
                            "queued": True,
                            "outbox_job_id": str(outbox_job_id),
                            "error": "Meta Graph API delivery failed",
                            "status_code": status_code,
                        }
                    else:
                        await erp_repository.mark_outbox_job_completed(
                            outbox_job_id,
                            tenant_id=tenant_id,
                        )
                except Exception as exc:
                    status_code = (
                        exc.response.status_code
                        if isinstance(exc, httpx.HTTPStatusError)
                        else None
                    )
                    logger.warning(
                        "Meta Graph API delivery failed (outbox_job_id=%s, error_type=%s, status_code=%s)",
                        outbox_job_id,
                        type(exc).__name__,
                        status_code,
                    )
                    await erp_repository.mark_outbox_job_failed(
                        outbox_job_id,
                        error_message=(
                            f"Meta Graph API delivery failed ({type(exc).__name__}, status_code={status_code})"
                        ),
                        tenant_id=tenant_id,
                    )
                    delivery = {
                        "delivered": False,
                        "queued": True,
                        "outbox_job_id": str(outbox_job_id),
                        "error": "Meta Graph API delivery failed",
                        "error_type": type(exc).__name__,
                        "status_code": status_code,
                    }

                processed_events.append(
                    {
                        "type": "private_reply",
                        "tenant_id": tenant_id,
                        "comment_id": comment_id,
                        "reply_text": rag_result["reply_text"],
                        "outbox_job_id": str(outbox_job_id),
                        "meta_delivery": delivery,
                    }
                )

    return {
        "status": "EVENT_RECEIVED",
        "processed_count": len(processed_events),
        "events": processed_events,
    }


@router.post("/sepay")
async def handle_sepay_ipn(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
    x_sepay_signature: str | None = Header(default=None, alias="X-SePay-Signature"),
    x_sepay_timestamp: str | None = Header(default=None, alias="X-SePay-Timestamp"),
) -> dict[str, Any]:
    """Process SePay bank transaction webhook (`developer.sepay.vn/vi/sepay-webhooks/tich-hop-webhook`)."""
    raw_body = await request.body()
    if not sepay_client.verify_webhook_request(
        raw_body=raw_body,
        authorization_header=authorization,
        signature_header=x_sepay_signature,
        timestamp_header=x_sepay_timestamp,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid SePay webhook authentication",
        )

    try:
        payload = SePayIPNWebhookPayload.model_validate_json(raw_body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc

    settings = get_settings()
    is_prod = _is_production_env()
    resolved_tenant = await erp_repository.resolve_channel_tenant(
        "sepay_account",
        payload.accountNumber or "",
        payload.subAccount or "",
    )
    if resolved_tenant is None:
        if is_prod:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unregistered SePay bank account channel",
            )
        tenant_id = settings.default_tenant_id
    else:
        tenant_id = resolved_tenant

    raw_payload_dict = json.loads(raw_body.decode("utf-8"))
    transaction_id = str(payload.id)
    order_code = sepay_client.extract_order_code(
        content=payload.content,
        explicit_code=payload.code,
    )

    pg_active = await erp_repository.is_postgres_active()
    existing_log = await _fetch_sepay_webhook_log(transaction_id=transaction_id, tenant_id=tenant_id)
    if existing_log is not None or (
        not pg_active and sepay_client.is_transaction_processed(transaction_id)
    ):
        existing_order = (
            await _fetch_order_by_code(order_code=order_code, tenant_id=tenant_id)
            if order_code
            else None
        )
        is_processed = bool(existing_log and existing_log.get("processing_status") == "PROCESSED")
        telegram_dispatch: dict[str, Any] | None = None
        if is_processed:
            target_employees = await _select_target_employees(
                assigned_employee_id=(
                    existing_order.assigned_employee_id if existing_order else None
                ),
                tenant_id=tenant_id,
            )
            primary_emp = target_employees[0] if target_employees else None
            telegram_dispatch = {
                "dispatched": True,
                "simulated": True,
                "idempotent_duplicate": True,
                "telegram_user_id": primary_emp.telegram_user_id if primary_emp else "",
                "order_code": order_code,
            }
        return {
            "success": True,
            "idempotent_duplicate": True,
            "order_code": existing_order.order_code if existing_order else order_code,
            "order_status": (
                existing_order.status.value
                if existing_order
                else (existing_log.get("processing_status", "IGNORED") if existing_log else "PAID")
            ),
            "sepay_transaction_id": transaction_id,
            "telegram_dispatch": telegram_dispatch,
        }

    if payload.transferType.strip().lower() != "in":
        inserted, _ = await _persist_sepay_event(
            transaction_id=transaction_id,
            tenant_id=tenant_id,
            order_code=order_code,
            payload=payload,
            raw_payload_dict=raw_payload_dict,
            processing_status="IGNORED_OUTBOUND",
            mark_paid=False,
        )
        return {
            "success": True,
            "idempotent_duplicate": not inserted,
            "ignored": True,
            "reason": "outbound_transfer",
            "sepay_transaction_id": transaction_id,
        }

    if not sepay_client.matches_configured_account(payload.accountNumber, payload.subAccount):
        inserted, _ = await _persist_sepay_event(
            transaction_id=transaction_id,
            tenant_id=tenant_id,
            order_code=order_code,
            payload=payload,
            raw_payload_dict=raw_payload_dict,
            processing_status="IGNORED_ACCOUNT_MISMATCH",
            mark_paid=False,
        )
        return {
            "success": True,
            "idempotent_duplicate": not inserted,
            "ignored": True,
            "reason": "account_mismatch",
            "sepay_transaction_id": transaction_id,
        }

    if not order_code:
        inserted, _ = await _persist_sepay_event(
            transaction_id=transaction_id,
            tenant_id=tenant_id,
            order_code=None,
            payload=payload,
            raw_payload_dict=raw_payload_dict,
            processing_status="IGNORED_NO_ORDER_CODE",
            mark_paid=False,
        )
        return {
            "success": True,
            "idempotent_duplicate": not inserted,
            "ignored": True,
            "reason": "no_order_code",
            "sepay_transaction_id": transaction_id,
        }

    existing_order = await _fetch_order_by_code(order_code=order_code, tenant_id=tenant_id)
    if existing_order is None:
        inserted, _ = await _persist_sepay_event(
            transaction_id=transaction_id,
            tenant_id=tenant_id,
            order_code=order_code,
            payload=payload,
            raw_payload_dict=raw_payload_dict,
            processing_status="UNMATCHED_ORDER",
            mark_paid=False,
        )
        return {
            "success": True,
            "idempotent_duplicate": not inserted,
            "ignored": True,
            "reason": "order_not_found",
            "order_code": order_code,
            "sepay_transaction_id": transaction_id,
        }

    if existing_order.status != OrderStatus.PENDING_PAYMENT:
        is_cancelled = existing_order.status == OrderStatus.CANCELLED
        proc_status = "IGNORED_CANCELLED" if is_cancelled else "IGNORED_ALREADY_PAID"
        reason = "order_cancelled" if is_cancelled else "order_already_paid"
        inserted, _ = await _persist_sepay_event(
            transaction_id=transaction_id,
            tenant_id=tenant_id,
            order_code=existing_order.order_code,
            payload=payload,
            raw_payload_dict=raw_payload_dict,
            processing_status=proc_status,
            mark_paid=False,
        )
        return {
            "success": True,
            "idempotent_duplicate": not inserted,
            "ignored": True,
            "reason": reason,
            "order_code": existing_order.order_code,
            "order_status": existing_order.status.value,
            "sepay_transaction_id": transaction_id,
        }

    if payload.transferAmount < existing_order.total_amount_vnd:
        inserted, _ = await _persist_sepay_event(
            transaction_id=transaction_id,
            tenant_id=tenant_id,
            order_code=existing_order.order_code,
            payload=payload,
            raw_payload_dict=raw_payload_dict,
            processing_status="UNDERPAID",
            mark_paid=False,
        )
        return {
            "success": True,
            "idempotent_duplicate": not inserted,
            "payment_verified": False,
            "reason": "underpayment",
            "order_code": existing_order.order_code,
            "order_status": existing_order.status.value,
            "expected_amount_vnd": existing_order.total_amount_vnd,
            "received_amount_vnd": payload.transferAmount,
            "sepay_transaction_id": transaction_id,
        }

    inserted, updated_order = await _persist_sepay_event(
        transaction_id=transaction_id,
        tenant_id=tenant_id,
        order_code=existing_order.order_code,
        payload=payload,
        raw_payload_dict=raw_payload_dict,
        processing_status="PROCESSED",
        mark_paid=True,
    )
    if not inserted:
        return {
            "success": True,
            "idempotent_duplicate": True,
            "order_code": existing_order.order_code,
            "order_status": existing_order.status.value,
            "sepay_transaction_id": transaction_id,
        }

    if updated_order is None or updated_order.status != OrderStatus.PAID:
        return {
            "success": True,
            "idempotent_duplicate": False,
            "ignored": True,
            "reason": "order_not_pending_payment",
            "order_code": existing_order.order_code,
            "order_status": updated_order.status.value if updated_order else existing_order.status.value,
            "sepay_transaction_id": transaction_id,
        }

    target_employees = await _select_target_employees(
        assigned_employee_id=updated_order.assigned_employee_id,
        tenant_id=tenant_id,
    )
    telegram_dispatch_result: dict[str, Any] | None = None
    if target_employees:
        items_summary = ", ".join(
            f"{item.get('quantity', 1)}x {item.get('sku', 'ITEM')}"
            for item in updated_order.items_json
        )
        has_live_bot = bool(
            telegram_dispatch_client.bot_token
            and not telegram_dispatch_client.bot_token.startswith("123456789:")
        )
        outbox_job_ids: list[str] = []
        dispatches: list[tuple[UUID, str]] = []
        for employee in target_employees:
            job_id = await erp_repository.enqueue_outbox_job(
                job_type="telegram_dispatch",
                payload={
                    "order_id": str(updated_order.id),
                    "order_code": updated_order.order_code,
                    "total_amount_vnd": updated_order.total_amount_vnd,
                    "items_summary": items_summary,
                    "employee_id": str(employee.id),
                    "telegram_user_id": employee.telegram_user_id,
                },
                tenant_id=tenant_id,
            )
            outbox_job_ids.append(str(job_id))
            dispatches.append((job_id, employee.telegram_user_id))

        background_tasks.add_task(
            _dispatch_sepay_telegram_alerts,
            dispatches=dispatches,
            order_code=updated_order.order_code,
            total_amount_vnd=updated_order.total_amount_vnd,
            items_summary=items_summary,
            tenant_id=tenant_id,
        )

        primary_employee = target_employees[0]
        telegram_dispatch_result = {
            "dispatched": True,
            "queued": True,
            "simulated": not has_live_bot,
            "telegram_user_id": primary_employee.telegram_user_id,
            "recipient_count": len(target_employees),
            "order_code": updated_order.order_code,
            "outbox_job_ids": outbox_job_ids,
        }

    return {
        "success": True,
        "idempotent_duplicate": False,
        "order_code": updated_order.order_code,
        "order_status": updated_order.status.value,
        "sepay_transaction_id": updated_order.sepay_transaction_id,
        "telegram_dispatch": telegram_dispatch_result,
    }
