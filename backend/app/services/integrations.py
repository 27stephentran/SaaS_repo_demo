"""Async `httpx` integration clients for Meta Graph API v18.0+, SePay VietQR, and Telegram Bot API."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import re
import time
from typing import Any
from urllib.parse import quote

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_DEFAULT_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
_DEFAULT_HTTP_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20)


def _is_retryable_httpx_error(exc: BaseException) -> bool:
    """Return True for transient httpx transport errors and retryable 429/5xx HTTP statuses."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


class MetaGraphClient:
    """Async client for Meta Graph API (v18.0+) Send API & Private Replies API."""

    def __init__(self) -> None:
        settings = get_settings()
        self.api_version = settings.meta_api_version
        self.access_token = settings.meta_access_token
        self.app_secret = settings.meta_app_secret
        self.base_url = f"https://graph.facebook.com/{self.api_version}"
        self._client: httpx.AsyncClient | None = None
        self._client_cls: Any = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily initialize and return a shared pooled `httpx.AsyncClient`."""
        current_cls = httpx.AsyncClient
        if (
            self._client is None
            or getattr(self._client, "is_closed", False)
            or self._client_cls is not current_cls
        ):
            old_client = self._client
            self._client = current_cls(
                timeout=_DEFAULT_HTTP_TIMEOUT,
                limits=_DEFAULT_HTTP_LIMITS,
            )
            self._client_cls = current_cls
            if old_client is not None and not getattr(old_client, "is_closed", False):
                if hasattr(old_client, "aclose"):
                    try:
                        await old_client.aclose()
                    except Exception:
                        pass
        return self._client

    async def aclose(self) -> None:
        """Close the shared pooled `httpx.AsyncClient` if initialized."""
        client = self._client
        self._client = None
        self._client_cls = None
        if client is not None and not getattr(client, "is_closed", False):
            if hasattr(client, "aclose"):
                await client.aclose()

    async def _post_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """POST JSON payload with bounded exponential backoff + jitter for transient failures."""
        resp: httpx.Response | None = None
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=0.25, max=2.0),
            retry=retry_if_exception(_is_retryable_httpx_error),
            reraise=True,
        ):
            with attempt:
                client = await self._get_client()
                resp = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
        assert resp is not None
        return resp.json()

    def verify_webhook_signature(
        self, raw_body: bytes, signature_header: str | None
    ) -> bool:
        """Verify Meta `X-Hub-Signature-256` HMAC-SHA256 signature over raw request body (fail-closed)."""
        secret = (self.app_secret or "").strip()
        if not secret or not signature_header:
            return False
        sig = signature_header.strip()
        if not sig.lower().startswith("sha256="):
            return False
        expected_hex = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        expected_sig = f"sha256={expected_hex}"
        return hmac.compare_digest(sig.lower(), expected_sig.lower())

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
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            return await self._post_with_retry(
                url,
                headers=headers,
                payload=payload,
            )
        except httpx.HTTPError as exc:
            status_code = (
                exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            )
            logger.warning(
                "Meta Graph API send_message failed (error_type=%s, status_code=%s)",
                type(exc).__name__,
                status_code,
            )
            return {
                "delivered": False,
                "error": "Meta Graph API HTTP error",
                "status_code": status_code,
            }

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
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            return await self._post_with_retry(
                url,
                headers=headers,
                payload=payload,
            )
        except httpx.HTTPError as exc:
            status_code = (
                exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            )
            logger.warning(
                "Meta Graph API send_private_reply failed (error_type=%s, status_code=%s)",
                type(exc).__name__,
                status_code,
            )
            return {
                "delivered": False,
                "error": "Meta Graph API HTTP error",
                "status_code": status_code,
            }


class SePayClient:
    """SePay Webhooks & Dynamic VietQR client aligned with developer.sepay.vn specs."""

    ORDER_CODE_PATTERN = re.compile(r"\b(DH\d+)\b", re.IGNORECASE)
    PG_SIGNED_FIELDS_ORDER: tuple[str, ...] = (
        "order_amount",
        "merchant",
        "currency",
        "operation",
        "order_description",
        "order_invoice_number",
        "customer_id",
        "payment_method",
        "success_url",
        "error_url",
        "cancel_url",
    )
    PG_CHECKOUT_SANDBOX_URL = "https://pay-sandbox.sepay.vn/v1/checkout/init"
    PG_CHECKOUT_PROD_URL = "https://pay.sepay.vn/v1/checkout/init"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.sepay_api_key
        self.account_number = settings.sepay_account_number
        self.bank_code = settings.sepay_bank_code
        self.webhook_secret = settings.sepay_webhook_secret
        self.auth_mode = settings.sepay_auth_mode.lower()
        self.payment_prefix = settings.sepay_payment_prefix or "DH"
        self.memo_prefix = settings.sepay_memo_prefix
        self.qr_template = settings.sepay_qr_template
        self.timestamp_tolerance_seconds = settings.sepay_timestamp_tolerance_seconds
        self.strict_account_check = settings.sepay_strict_account_check
        self.pg_merchant_id = settings.sepay_pg_merchant_id
        self.pg_secret_key = settings.sepay_pg_secret_key
        self.pg_ipn_secret = settings.sepay_pg_ipn_secret
        self.pg_env = (settings.sepay_pg_env or "sandbox").strip().lower()
        self._order_code_pattern = re.compile(
            rf"\b({re.escape(self.payment_prefix)}\d+)\b",
            re.IGNORECASE,
        )
        self._processed_transactions: set[str] = set()

    def generate_vietqr_url(self, amount_vnd: int, order_code: str) -> str:
        """Generate a dynamic SePay VietQR image URL embedding amount, memo prefix, and order code."""
        prefix = (self.memo_prefix or "").strip()
        clean_code = order_code.strip().upper()
        memo_text = f"{prefix} {clean_code}".strip() if prefix else clean_code
        encoded_desc = quote(memo_text)
        url = (
            f"https://qr.sepay.vn/img?acc={quote(self.account_number)}"
            f"&bank={quote(self.bank_code)}&amount={amount_vnd}&des={encoded_desc}"
        )
        if self.qr_template:
            url += f"&template={quote(self.qr_template)}"
        return url

    def extract_order_code(self, content: str | None, explicit_code: str | None = None) -> str | None:
        """Extract canonical order code (e.g. `DH1001`) preferring SePay-parsed `code` over content regex."""
        payment_prefix = (self.payment_prefix or "DH").strip() or "DH"
        memo_prefix = (self.memo_prefix or "").strip()

        if explicit_code and explicit_code.strip():
            cleaned_explicit = explicit_code.strip().upper()
            explicit_match = re.search(
                rf"({re.escape(payment_prefix)}\d+)\b",
                cleaned_explicit,
                re.IGNORECASE,
            )
            if explicit_match and memo_prefix and cleaned_explicit.startswith(memo_prefix.upper()):
                return explicit_match.group(1).upper()
            return cleaned_explicit

        text = content or ""
        if not text:
            return None

        # 1. Standard word-boundary match (e.g. "SEVQR DH1001" or "chuyen khoan DH1001")
        word_pattern = re.compile(rf"\b({re.escape(payment_prefix)}\d+)\b", re.IGNORECASE)
        match = word_pattern.search(text)
        if match:
            return match.group(1).upper()

        # 2. Configured memo_prefix + optional space + order code (e.g. "SEVQRDH1001" or "SEVQR DH1001")
        if memo_prefix:
            prefixed_pattern = re.compile(
                rf"{re.escape(memo_prefix)}\s*({re.escape(payment_prefix)}\d+)\b",
                re.IGNORECASE,
            )
            prefixed_match = prefixed_pattern.search(text)
            if prefixed_match:
                return prefixed_match.group(1).upper()

        # 3. Fallback for smushed/non-spaced bank memos (e.g. "ABC123DH1001")
        fallback_pattern = re.compile(rf"({re.escape(payment_prefix)}\d+)\b", re.IGNORECASE)
        fallback_match = fallback_pattern.search(text)
        if fallback_match:
            return fallback_match.group(1).upper()
        return None

    def verify_webhook_authorization(self, authorization_header: str | None) -> bool:
        """Verify SePay `Authorization: Apikey <SECRET>` header using constant-time comparison."""
        if not authorization_header or not self.webhook_secret:
            return False
        expected = f"Apikey {self.webhook_secret}"
        return hmac.compare_digest(authorization_header.strip(), expected)

    def verify_webhook_hmac(
        self,
        raw_body: bytes,
        signature_header: str | None,
        timestamp_header: str | None,
        now_ts: int | None = None,
    ) -> bool:
        """Verify SePay HMAC-SHA256 (`X-SePay-Signature` / `X-SePay-Timestamp`) over raw request body."""
        if not self.webhook_secret or not signature_header or not timestamp_header:
            return False
        try:
            timestamp_val = int(timestamp_header.strip())
        except ValueError:
            return False

        current_ts = int(time.time()) if now_ts is None else now_ts
        if abs(current_ts - timestamp_val) > self.timestamp_tolerance_seconds:
            return False

        signed_payload = f"{timestamp_val}.".encode() + raw_body
        expected_hex = hmac.new(
            self.webhook_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        expected_sig = f"sha256={expected_hex}"
        return hmac.compare_digest(signature_header.strip().lower(), expected_sig.lower())

    def verify_webhook_request(
        self,
        raw_body: bytes,
        authorization_header: str | None = None,
        signature_header: str | None = None,
        timestamp_header: str | None = None,
        now_ts: int | None = None,
    ) -> bool:
        """Verify inbound SePay webhook request using configured `sepay_auth_mode` (fail-closed)."""
        if not self.webhook_secret:
            return False

        mode = self.auth_mode
        has_hmac_headers = bool(signature_header or timestamp_header)

        if mode == "hmac":
            return self.verify_webhook_hmac(
                raw_body=raw_body,
                signature_header=signature_header,
                timestamp_header=timestamp_header,
                now_ts=now_ts,
            )
        if mode == "apikey":
            return self.verify_webhook_authorization(authorization_header)

        if mode == "both":
            # Default "both": if HMAC headers are present, enforce HMAC; otherwise verify API Key.
            if has_hmac_headers:
                return self.verify_webhook_hmac(
                    raw_body=raw_body,
                    signature_header=signature_header,
                    timestamp_header=timestamp_header,
                    now_ts=now_ts,
                )
            return self.verify_webhook_authorization(authorization_header)

        return False

    def matches_configured_account(
        self, account_number: str | None, sub_account: str | None
    ) -> bool:
        """Check whether webhook account fields match configured account when strict check is enabled."""
        if not self.strict_account_check or not self.account_number:
            return True
        expected = self.account_number.strip()
        candidates = [
            val.strip()
            for val in (account_number, sub_account)
            if val is not None and val.strip()
        ]
        return expected in candidates

    def is_transaction_processed(self, transaction_id: str) -> bool:
        """Check whether a SePay transaction ID has already been processed."""
        return transaction_id in self._processed_transactions

    def mark_transaction_processed(self, transaction_id: str) -> None:
        """Record a SePay transaction ID as processed for idempotency (`U-15`)."""
        self._processed_transactions.add(transaction_id)

    def sign_checkout_fields(
        self,
        fields: dict[str, Any],
        secret_key: str | None = None,
    ) -> str:
        """Sign ordered SePay Payment Gateway checkout fields with HMAC-SHA256 + Base64."""
        raw_key = self.pg_secret_key if secret_key is None else secret_key
        key = (raw_key or "").strip()
        if not key:
            raise ValueError("SEPAY_PG_SECRET_KEY is not configured")

        parts: list[str] = []
        for field_name in self.PG_SIGNED_FIELDS_ORDER:
            if field_name not in fields:
                continue
            val = fields[field_name]
            if val is None or val == "" or val == 0 or val == "0":
                continue
            if isinstance(val, str) and not val.strip():
                continue
            parts.append(f"{field_name}={val}")

        signed_str = ",".join(parts)
        digest = hmac.new(
            key.encode("utf-8"),
            signed_str.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("ascii")

    def build_checkout_form(
        self,
        order_code: str,
        amount_vnd: int,
        description: str,
        customer_id: str | None = None,
        payment_method: str | None = None,
        success_url: str | None = None,
        error_url: str | None = None,
        cancel_url: str | None = None,
        lang: str = "vi",
    ) -> dict[str, Any]:
        """Build a signed SePay Payment Gateway checkout form payload (fail-closed)."""
        merchant = (self.pg_merchant_id or "").strip()
        secret_key = (self.pg_secret_key or "").strip()
        if not merchant or not secret_key:
            raise ValueError("SePay Payment Gateway credentials are not configured")

        clean_amount = int(amount_vnd)
        if clean_amount <= 0:
            raise ValueError("Order amount must be positive for Payment Gateway checkout")

        raw_fields: dict[str, Any] = {
            "order_amount": str(clean_amount),
            "merchant": merchant,
            "currency": "VND",
            "operation": "PURCHASE",
            "order_description": (description or f"Thanh toan don hang {order_code}").strip(),
            "order_invoice_number": order_code.strip().upper(),
            "customer_id": customer_id,
            "payment_method": payment_method,
            "success_url": success_url,
            "error_url": error_url,
            "cancel_url": cancel_url,
        }

        ordered_fields: dict[str, Any] = {}
        for field_name in self.PG_SIGNED_FIELDS_ORDER:
            val = raw_fields.get(field_name)
            if val is None or val == "" or val == 0 or val == "0":
                continue
            if isinstance(val, str):
                stripped = val.strip()
                if not stripped or stripped == "0":
                    continue
                ordered_fields[field_name] = stripped
            else:
                ordered_fields[field_name] = val

        signature = self.sign_checkout_fields(ordered_fields, secret_key=secret_key)
        ordered_fields["signature"] = signature
        if lang and str(lang).strip():
            ordered_fields["lang"] = str(lang).strip()

        env = (self.pg_env or "sandbox").strip().lower()
        checkout_url = (
            self.PG_CHECKOUT_PROD_URL if env == "production" else self.PG_CHECKOUT_SANDBOX_URL
        )
        return {
            "checkout_url": checkout_url,
            "fields": ordered_fields,
        }

    def verify_pg_ipn_secret(self, x_secret_key: str | None) -> bool:
        """Verify SePay Payment Gateway IPN `X-Secret-Key` header using constant-time compare (fail-closed)."""
        expected = (self.pg_ipn_secret or "").strip()
        if not expected or not x_secret_key:
            return False
        return hmac.compare_digest(x_secret_key.strip(), expected)


class TelegramDispatchClient:
    """Lightweight Telegram Bot API (`sendMessage`) dispatcher for internal staff notifications."""

    def __init__(self) -> None:
        settings = get_settings()
        self.bot_token = settings.telegram_bot_token
        self._client: httpx.AsyncClient | None = None
        self._client_cls: Any = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily initialize and return a shared pooled `httpx.AsyncClient`."""
        current_cls = httpx.AsyncClient
        if (
            self._client is None
            or getattr(self._client, "is_closed", False)
            or self._client_cls is not current_cls
        ):
            old_client = self._client
            self._client = current_cls(
                timeout=_DEFAULT_HTTP_TIMEOUT,
                limits=_DEFAULT_HTTP_LIMITS,
            )
            self._client_cls = current_cls
            if old_client is not None and not getattr(old_client, "is_closed", False):
                if hasattr(old_client, "aclose"):
                    try:
                        await old_client.aclose()
                    except Exception:
                        pass
        return self._client

    async def aclose(self) -> None:
        """Close the shared pooled `httpx.AsyncClient` if initialized."""
        client = self._client
        self._client = None
        self._client_cls = None
        if client is not None and not getattr(client, "is_closed", False):
            if hasattr(client, "aclose"):
                await client.aclose()

    async def _post_with_retry(
        self,
        url: str,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """POST JSON payload with bounded exponential backoff + jitter for transient failures."""
        resp: httpx.Response | None = None
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=0.25, max=2.0),
            retry=retry_if_exception(_is_retryable_httpx_error),
            reraise=True,
        ):
            with attempt:
                client = await self._get_client()
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        assert resp is not None
        return resp.json()

    async def dispatch_order_alert(
        self,
        telegram_user_id: str,
        order_code: str,
        total_amount_vnd: int,
        items_summary: str,
        customer_name: str | None = None,
        sepay_reference: str | None = None,
    ) -> dict[str, Any]:
        """Send an instant HTML-escaped order fulfillment message to a staff member's `telegram_user_id`."""
        escaped_order_code = html.escape(str(order_code))
        escaped_amount = html.escape(f"{total_amount_vnd:,} VND")
        escaped_items = html.escape(str(items_summary))
        lines = [
            f"🔔 <b>[NEW PAID ORDER — {escaped_order_code}]</b>",
            f"💰 Amount: <code>{escaped_amount}</code> (SePay VietQR Confirmed)",
        ]
        if customer_name:
            lines.append(f"👤 Customer: {html.escape(str(customer_name))}")
        lines.append(f"📦 Items: {escaped_items}")
        if sepay_reference:
            lines.append(f"🧾 Reference: <code>{html.escape(str(sepay_reference))}</code>")
        lines.append(
            "⚡ Please pack and transition status to <b>FULFILLING</b> in the ERP Admin Panel."
        )
        message_text = "\n".join(lines)
        payload = {
            "chat_id": telegram_user_id,
            "text": message_text,
            "parse_mode": "HTML",
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
        try:
            telegram_response = await self._post_with_retry(url, payload=payload)
            return {
                "dispatched": True,
                "simulated": False,
                "telegram_user_id": telegram_user_id,
                "order_code": order_code,
                "telegram_response": telegram_response,
            }
        except httpx.HTTPError as exc:
            status_code = (
                exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            )
            logger.warning(
                "Telegram Bot API dispatch failed for order=%s (error_type=%s, status_code=%s)",
                order_code,
                type(exc).__name__,
                status_code,
            )
            return {
                "dispatched": False,
                "simulated": False,
                "telegram_user_id": telegram_user_id,
                "order_code": order_code,
                "error": "Telegram Bot API HTTP error",
                "status_code": status_code,
                "error_type": type(exc).__name__,
            }
        except Exception as exc:
            logger.warning(
                "Telegram Bot API dispatch failed unexpectedly for order=%s (error_type=%s)",
                order_code,
                type(exc).__name__,
            )
            return {
                "dispatched": False,
                "simulated": False,
                "telegram_user_id": telegram_user_id,
                "order_code": order_code,
                "error": "Telegram Bot API dispatch error",
                "status_code": None,
                "error_type": type(exc).__name__,
            }


meta_graph_client = MetaGraphClient()
sepay_client = SePayClient()
telegram_dispatch_client = TelegramDispatchClient()


async def close_integration_clients() -> None:
    """Close shared pooled `httpx.AsyncClient` instances across integration singletons."""
    await meta_graph_client.aclose()
    await telegram_dispatch_client.aclose()
