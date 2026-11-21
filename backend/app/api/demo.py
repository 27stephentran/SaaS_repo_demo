"""Demo sandbox API endpoints isolated to the `demo_api` permission group."""

from __future__ import annotations

import html
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.auth import SlidingWindowRateLimiter
from app.core.auth import AuthenticatedUser, require_demo_api
from app.core.config import get_settings, is_production_env
from app.models.entities import DemoTelegramNotifyRequest, TelegramDispatchResponse
from app.services.integrations import telegram_dispatch_client

demo_rate_limiter = SlidingWindowRateLimiter(window_seconds=60.0)

router = APIRouter(
    prefix="/api/v1/demo",
    tags=["Demo"],
    dependencies=[Depends(require_demo_api)],
)

DemoUserDep = Annotated[AuthenticatedUser, Depends(require_demo_api)]


def _build_demo_preview_text(payload: DemoTelegramNotifyRequest) -> str:
    escaped_order_code = html.escape(str(payload.order_code))
    escaped_amount = html.escape(f"{payload.total_amount_vnd:,} VND")
    escaped_items = html.escape(str(payload.items_summary))
    lines = [
        f"🔔 <b>[NEW PAID ORDER — {escaped_order_code}]</b>",
        f"💰 Amount: <code>{escaped_amount}</code> (SePay VietQR Confirmed)",
    ]
    if payload.customer_name:
        lines.append(f"👤 Customer: {html.escape(str(payload.customer_name))}")
    lines.append(f"📦 Items: {escaped_items}")
    ref = payload.sepay_reference or payload.order_code
    if ref:
        lines.append(f"🧾 Reference: <code>{html.escape(str(ref))}</code>")
    lines.append(
        "⚡ Please pack and transition status to <b>FULFILLING</b> in the ERP Admin Panel."
    )
    return "\n".join(lines)


@router.post("/notify-telegram", response_model=TelegramDispatchResponse)
async def demo_notify_telegram(
    request: Request,
    payload: DemoTelegramNotifyRequest,
    current_user: DemoUserDep,
) -> TelegramDispatchResponse:
    """Trigger a rate-limited Telegram fulfillment preview/notification from `/demo`."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    retry_after = demo_rate_limiter.check_and_record(
        keys=[
            f"ip:{client_ip}",
            f"demo:{current_user.user_id}",
        ],
        limit=settings.demo_notify_rate_limit_per_minute,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many demo notification attempts; please retry later",
            headers={"Retry-After": str(retry_after)},
        )

    configured_chat_id = settings.demo_telegram_chat_id.strip()
    if not configured_chat_id:
        if is_production_env():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="DEMO_TELEGRAM_CHAT_ID is not configured in production",
            )
        # Never invoke live Telegram Bot API against an untrusted client-supplied chat ID.
        preview_target = (payload.telegram_user_id or "").strip() or "demo-sandbox"
        return TelegramDispatchResponse(
            dispatched=True,
            simulated=True,
            telegram_user_id=preview_target,
            order_code=payload.order_code,
            preview_text=_build_demo_preview_text(payload),
            error=None,
            status_code=None,
        )

    result = await telegram_dispatch_client.dispatch_order_alert(
        telegram_user_id=configured_chat_id,
        order_code=payload.order_code,
        total_amount_vnd=payload.total_amount_vnd,
        items_summary=payload.items_summary,
        customer_name=payload.customer_name,
        sepay_reference=payload.sepay_reference or payload.order_code,
    )
    return TelegramDispatchResponse(
        dispatched=bool(result.get("dispatched", False)),
        simulated=bool(result.get("simulated", False)),
        telegram_user_id=str(result.get("telegram_user_id", configured_chat_id)),
        order_code=str(result.get("order_code", payload.order_code)),
        preview_text=result.get("preview_text") or result.get("text"),
        error=result.get("error"),
        status_code=result.get("status_code"),
    )
