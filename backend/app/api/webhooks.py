"""Omnichannel Webhook Endpoints for Meta Graph API (v18.0+) and SePay VietQR IPN."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.database import erp_repository
from app.models.entities import (
    OrderCreateRequest,
    OrderLineItem,
    SePayIPNWebhookPayload,
)
from app.services.ai_rag import sales_rag_engine
from app.services.integrations import (
    meta_graph_client,
    sepay_client,
    telegram_dispatch_client,
)

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])


@router.get("/meta", response_class=PlainTextResponse)
async def verify_meta_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
) -> PlainTextResponse:
    """Verify Meta Graph API v18.0+ webhook subscription challenge."""
    settings = get_settings()
    if hub_mode == "subscribe" and hmac.compare_digest(
        hub_verify_token, settings.meta_verify_token
    ):
        return PlainTextResponse(content=hub_challenge, status_code=status.HTTP_200_OK)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid Meta webhook verification token",
    )


@router.post("/meta")
async def handle_meta_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process inbound Meta Messenger/Instagram DMs and public comments via Gemini RAG."""
    entries = payload.get("entry", [])
    processed_events: list[dict[str, Any]] = []

    for entry in entries:
        # 1. Handle direct Messenger / Instagram messages
        for messaging_event in entry.get("messaging", []):
            sender_psid = messaging_event.get("sender", {}).get("id")
            message_text = messaging_event.get("message", {}).get("text")
            if not sender_psid or not message_text:
                continue

            customer = erp_repository.get_or_create_customer_by_psid(meta_psid=sender_psid)
            query_embedding = await sales_rag_engine.embed_text(message_text)
            matched_items = erp_repository.search_inventory_by_embedding(
                query_embedding=query_embedding,
                query_text=message_text,
                limit=3,
                embedding_fn=sales_rag_engine.embed_text_sync,
            )
            rag_result = await sales_rag_engine.generate_sales_reply(
                customer_message=message_text,
                matched_items=matched_items,
            )

            created_order_code: str | None = None
            sepay_qr_url: str | None = None

            if rag_result["order_intent_detected"] and matched_items:
                top_item = matched_items[0]
                existing_orders = erp_repository.list_orders()
                created_order_code = f"DH{1001 + len(existing_orders)}"
                sepay_qr_url = sepay_client.generate_vietqr_url(
                    amount_vnd=top_item.price_vnd,
                    order_code=created_order_code,
                )
                order_req = OrderCreateRequest(
                    customer_id=customer.id,
                    items=[
                        OrderLineItem(
                            sku=top_item.sku,
                            name=top_item.name,
                            quantity=1,
                            unit_price_vnd=top_item.price_vnd,
                        )
                    ],
                )
                erp_repository.create_order(
                    payload=order_req,
                    sepay_qr_url=sepay_qr_url,
                    order_code=created_order_code,
                )
                rag_result["reply_text"] += (
                    f"\n\n💳 Mã đơn hàng: {created_order_code} — Quét VietQR để thanh toán: {sepay_qr_url}"
                )

            delivery = await meta_graph_client.send_message(
                recipient_psid=sender_psid,
                text=rag_result["reply_text"],
            )
            processed_events.append(
                {
                    "type": "messaging",
                    "sender_psid": sender_psid,
                    "reply_text": rag_result["reply_text"],
                    "model_used": rag_result["model_used"],
                    "order_intent_detected": rag_result["order_intent_detected"],
                    "order_code": created_order_code,
                    "sepay_qr_url": sepay_qr_url,
                    "meta_delivery": delivery,
                }
            )

        # 2. Handle public post comment events via Private Replies API
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if change.get("field") == "feed" and value.get("item") == "comment":
                comment_id = value.get("comment_id")
                comment_text = value.get("message", "")
                if not comment_id or not comment_text:
                    continue
                query_embedding = await sales_rag_engine.embed_text(comment_text)
                matched_items = erp_repository.search_inventory_by_embedding(
                    query_embedding=query_embedding,
                    query_text=comment_text,
                    limit=2,
                    embedding_fn=sales_rag_engine.embed_text_sync,
                )
                rag_result = await sales_rag_engine.generate_sales_reply(
                    customer_message=comment_text,
                    matched_items=matched_items,
                )
                delivery = await meta_graph_client.send_private_reply(
                    comment_id=comment_id,
                    text=rag_result["reply_text"],
                )
                processed_events.append(
                    {
                        "type": "private_reply",
                        "comment_id": comment_id,
                        "reply_text": rag_result["reply_text"],
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
    payload: SePayIPNWebhookPayload,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Process SePay IPN payment callback, mark order PAID, and dispatch Telegram staff alert."""
    if not sepay_client.verify_webhook_authorization(authorization):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid SePay webhook authorization header",
        )

    order_code = sepay_client.extract_order_code(
        content=payload.content,
        explicit_code=payload.code,
    )
    if not order_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract order code (e.g. DH1001) from SePay transfer content",
        )

    transaction_id = str(payload.id)
    if sepay_client.is_transaction_processed(transaction_id):
        existing_orders = [
            o for o in erp_repository.list_orders() if o.order_code.upper() == order_code.upper()
        ]
        existing_order = existing_orders[0] if existing_orders else None
        employee = (
            erp_repository.get_employee(existing_order.assigned_employee_id)
            if existing_order
            else erp_repository.get_employee(None)
        )
        return {
            "success": True,
            "idempotent_duplicate": True,
            "order_code": existing_order.order_code if existing_order else order_code,
            "order_status": existing_order.status.value if existing_order else "PAID",
            "sepay_transaction_id": transaction_id,
            "telegram_dispatch": {
                "dispatched": True,
                "simulated": True,
                "idempotent_duplicate": True,
                "telegram_user_id": employee.telegram_user_id if employee else "",
                "order_code": order_code,
            },
        }

    updated_order = erp_repository.mark_order_paid_by_code(
        order_code=order_code,
        transaction_id=transaction_id,
    )
    if updated_order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with code {order_code} not found",
        )

    sepay_client.mark_transaction_processed(transaction_id)

    employee = erp_repository.get_employee(updated_order.assigned_employee_id)
    telegram_dispatch_result: dict[str, Any] | None = None
    if employee is not None:
        items_summary = ", ".join(
            f"{item.get('quantity', 1)}x {item.get('sku', 'ITEM')}"
            for item in updated_order.items_json
        )
        telegram_dispatch_result = await telegram_dispatch_client.dispatch_order_alert(
            telegram_user_id=employee.telegram_user_id,
            order_code=updated_order.order_code,
            total_amount_vnd=updated_order.total_amount_vnd,
            items_summary=items_summary,
        )

    return {
        "success": True,
        "idempotent_duplicate": False,
        "order_code": updated_order.order_code,
        "order_status": updated_order.status.value,
        "sepay_transaction_id": updated_order.sepay_transaction_id,
        "telegram_dispatch": telegram_dispatch_result,
    }
