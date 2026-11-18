"""Async `httpx` integration clients for Meta Graph API v18.0+, SePay VietQR, and Telegram Bot API."""

from __future__ import annotations

import hmac
import re
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings


class MetaGraphClient:
    """Async client for Meta Graph API (v18.0+) Send API & Private Replies API."""

    def __init__(self) -> None:
        settings = get_settings()
        self.api_version = settings.meta_api_version
        self.access_token = settings.meta_access_token
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    async def send_message(self, recipient_psid: str, text: str) -> dict[str, Any]:
        """Send a Messenger/Instagram DM reply via Meta Send API."""
        payload = {
            "recipient": {"id": recipient_psid},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        }
        if not self.access_token:
            return {
                "simulated": True,
                "api_version": self.api_version,
                "recipient_id": recipient_psid,
                "message_text": text,
            }

        url = f"{self.base_url}/me/messages"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                params={"access_token": self.access_token},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def send_private_reply(self, comment_id: str, text: str) -> dict[str, Any]:
        """Send a Private Reply DM to a public Facebook/Instagram post comment."""
        payload = {
            "recipient": {"comment_id": comment_id},
            "message": {"text": text},
        }
        if not self.access_token:
            return {
                "simulated": True,
                "api_version": self.api_version,
                "comment_id": comment_id,
                "message_text": text,
            }

        url = f"{self.base_url}/me/messages"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                params={"access_token": self.access_token},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()


class SePayClient:
    """SePay Sandbox API client for dynamic VietQR generation and IPN order code extraction."""

    ORDER_CODE_PATTERN = re.compile(r"\b(DH\d+)\b", re.IGNORECASE)

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.sepay_api_key
        self.account_number = settings.sepay_account_number
        self.bank_code = settings.sepay_bank_code
        self.webhook_secret = settings.sepay_webhook_secret
        self._processed_transactions: set[str] = set()

    def generate_vietqr_url(self, amount_vnd: int, order_code: str) -> str:
        """Generate a dynamic SePay VietQR image URL embedding amount and order code."""
        encoded_desc = quote(order_code.upper())
        return (
            f"https://qr.sepay.vn/img?acc={self.account_number}"
            f"&bank={self.bank_code}&amount={amount_vnd}&des={encoded_desc}"
        )

    def extract_order_code(self, content: str, explicit_code: str | None = None) -> str | None:
        """Extract canonical order code (e.g. `DH1001`) from bank transfer description."""
        if explicit_code and explicit_code.strip():
            return explicit_code.strip().upper()
        match = self.ORDER_CODE_PATTERN.search(content or "")
        if match:
            return match.group(1).upper()
        return None

    def verify_webhook_authorization(self, authorization_header: str | None) -> bool:
        """Verify SePay IPN `Authorization: Apikey <SECRET>` header using constant-time comparison.

        Fails closed (`False`) when the header or secret is missing.
        """
        if not authorization_header or not self.webhook_secret:
            return False
        expected = f"Apikey {self.webhook_secret}"
        return hmac.compare_digest(authorization_header.strip(), expected)

    def is_transaction_processed(self, transaction_id: str) -> bool:
        """Check whether a SePay IPN transaction ID has already been processed."""
        return transaction_id in self._processed_transactions

    def mark_transaction_processed(self, transaction_id: str) -> None:
        """Record a SePay IPN transaction ID as processed for idempotency (`U-15`)."""
        self._processed_transactions.add(transaction_id)


class TelegramDispatchClient:
    """Lightweight Telegram Bot API (`sendMessage`) dispatcher for internal staff notifications."""

    def __init__(self) -> None:
        settings = get_settings()
        self.bot_token = settings.telegram_bot_token

    async def dispatch_order_alert(
        self,
        telegram_user_id: str,
        order_code: str,
        total_amount_vnd: int,
        items_summary: str,
    ) -> dict[str, Any]:
        """Send an instant order fulfillment message to a staff member's `telegram_user_id`."""
        message_text = (
            f"🔔 *[NEW PAID ORDER — {order_code}]*\n"
            f"💰 Amount: `{total_amount_vnd:,} VND` (SePay VietQR Confirmed)\n"
            f"📦 Items: {items_summary}\n"
            f"⚡ Please pack and transition status to *FULFILLING* in the ERP Admin Panel."
        )
        payload = {
            "chat_id": telegram_user_id,
            "text": message_text,
            "parse_mode": "Markdown",
        }

        if not self.bot_token or self.bot_token.startswith("123456789:"):
            return {
                "dispatched": True,
                "simulated": True,
                "telegram_user_id": telegram_user_id,
                "order_code": order_code,
                "text": message_text,
            }

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return {
                "dispatched": True,
                "simulated": False,
                "telegram_user_id": telegram_user_id,
                "order_code": order_code,
                "telegram_response": resp.json(),
            }


meta_graph_client = MetaGraphClient()
sepay_client = SePayClient()
telegram_dispatch_client = TelegramDispatchClient()
