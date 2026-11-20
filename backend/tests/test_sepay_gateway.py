"""Comprehensive regression tests for SePay Payment Gateway checkout signing, IPN webhook, and config posture."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.config import get_settings
from app.core.database import erp_repository
from app.main import app
from app.models.entities import OrderStatus
from app.services.integrations import SePayClient, sepay_client, telegram_dispatch_client

REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDED_ORDER_ID = UUID("44444444-4444-4444-4444-444444444401")
SEEDED_CUSTOMER_ID = UUID("11111111-1111-1111-1111-111111111111")
TEST_PG_MERCHANT_ID = "SP-MERCHANT-TEST-01"
TEST_PG_SECRET_KEY = "unit-test-pg-secret-key-0123456789"
TEST_PG_IPN_SECRET = "unit-test-pg-ipn-secret-0123456789"


def _build_gateway_ipn_payload(
    *,
    notification_type: str = "ORDER_PAID",
    order_code: str | None = "DH1001",
    order_status: str | None = "CAPTURED",
    order_currency: str | None = "VND",
    order_amount: str | int = "640000",
    transaction_id: str | None = "PG-TX-100001",
    transaction_status: str | None = "APPROVED",
    transaction_amount: str | int = "640000",
    transaction_currency: str | None = "VND",
    merchant: str | None = TEST_PG_MERCHANT_ID,
) -> dict[str, Any]:
    return {
        "timestamp": 1763625600,
        "notification_type": notification_type,
        "order": {
            "id": "ord-pg-001",
            "order_id": order_code,
            "order_status": order_status,
            "order_currency": order_currency,
            "order_amount": order_amount,
            "order_invoice_number": order_code,
            "order_description": f"Thanh toan don hang {order_code or ''}".strip(),
            "merchant": merchant,
        },
        "transaction": {
            "id": "tx-pg-row-001",
            "payment_method": "BANK_TRANSFER",
            "transaction_id": transaction_id,
            "transaction_type": "PAYMENT",
            "transaction_date": "2026-11-20 08:00:00",
            "transaction_status": transaction_status,
            "transaction_amount": transaction_amount,
            "transaction_currency": transaction_currency,
        },
        "customer": {
            "id": "cust-pg-001",
            "customer_id": str(SEEDED_CUSTOMER_ID),
        },
    }


# ------------------------------------------------------------------------------
# 1. SePayClient.sign_checkout_fields & Checkout URLs
# ------------------------------------------------------------------------------


def test_sign_checkout_fields_ordering_skip_rules_and_hmac_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify exact PG field ordering, skipping empty/0/None values, and Base64(HMAC-SHA256) vector."""
    assert SePayClient.PG_SIGNED_FIELDS_ORDER == (
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

    secret = "unit-test-pg-secret-key"
    monkeypatch.setenv("SEPAY_PG_SECRET_KEY", secret)
    get_settings.cache_clear()
    client = SePayClient()

    # Pass keys in reverse/shuffled order plus skipped values (0, "0", "", "   ", None) and extra keys.
    shuffled_fields: dict[str, Any] = {
        "extra_unsigned_field": "ignore-me",
        "lang": "vi",
        "cancel_url": "   ",
        "error_url": None,
        "success_url": "https://shop.example/success",
        "payment_method": "0",
        "customer_id": 0,
        "order_invoice_number": "DH1001",
        "order_description": "Pay DH1001",
        "operation": "PURCHASE",
        "currency": "VND",
        "merchant": "SP-MERCHANT-01",
        "order_amount": "640000",
    }

    expected_signed_str = (
        "order_amount=640000,"
        "merchant=SP-MERCHANT-01,"
        "currency=VND,"
        "operation=PURCHASE,"
        "order_description=Pay DH1001,"
        "order_invoice_number=DH1001,"
        "success_url=https://shop.example/success"
    )
    independent_digest = hmac.new(
        secret.encode("utf-8"),
        expected_signed_str.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    independent_b64 = base64.b64encode(independent_digest).decode("ascii")
    hardcoded_vector = "IojVs+hziWQGR0B9kHpLF2iNVxtUa9lNKC10Wdr/woY="
    assert independent_b64 == hardcoded_vector

    sig = client.sign_checkout_fields(shuffled_fields)
    assert sig == hardcoded_vector

    # Changing insertion order or swapping skipped representations must produce the exact same signature.
    reordered_fields: dict[str, Any] = {
        "order_amount": "640000",
        "merchant": "SP-MERCHANT-01",
        "currency": "VND",
        "operation": "PURCHASE",
        "order_description": "Pay DH1001",
        "order_invoice_number": "DH1001",
        "customer_id": "",
        "payment_method": None,
        "success_url": "https://shop.example/success",
        "error_url": 0,
        "cancel_url": "0",
        "lang": "en",
    }
    assert client.sign_checkout_fields(reordered_fields) == hardcoded_vector

    # Altering an included field changes the signature.
    tampered_fields = dict(reordered_fields)
    tampered_fields["order_amount"] = "640001"
    assert client.sign_checkout_fields(tampered_fields) != hardcoded_vector

    # Explicit empty secret key must fail closed.
    monkeypatch.setenv("SEPAY_PG_SECRET_KEY", "")
    get_settings.cache_clear()
    unconfigured_client = SePayClient()
    with pytest.raises(ValueError, match="SEPAY_PG_SECRET_KEY"):
        unconfigured_client.sign_checkout_fields(reordered_fields)


def test_checkout_url_sandbox_vs_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify sandbox vs production Payment Gateway checkout URLs and skipped empty fields in form."""
    monkeypatch.setenv("SEPAY_PG_MERCHANT_ID", TEST_PG_MERCHANT_ID)
    monkeypatch.setenv("SEPAY_PG_SECRET_KEY", TEST_PG_SECRET_KEY)

    monkeypatch.setenv("SEPAY_PG_ENV", "sandbox")
    get_settings.cache_clear()
    sandbox_client = SePayClient()
    sandbox_form = sandbox_client.build_checkout_form(
        order_code="dh1001",
        amount_vnd=640000,
        description="Thanh toan don hang DH1001",
        customer_id="",
        payment_method=None,
        success_url="https://shop.example/ok",
        error_url="0",
        cancel_url="   ",
    )
    assert sandbox_form["checkout_url"] == "https://pay-sandbox.sepay.vn/v1/checkout/init"
    assert sandbox_form["fields"]["order_invoice_number"] == "DH1001"
    assert "customer_id" not in sandbox_form["fields"]
    assert "payment_method" not in sandbox_form["fields"]
    assert "error_url" not in sandbox_form["fields"]
    assert "cancel_url" not in sandbox_form["fields"]
    assert sandbox_form["fields"]["success_url"] == "https://shop.example/ok"
    assert sandbox_form["fields"]["signature"] == sandbox_client.sign_checkout_fields(
        sandbox_form["fields"]
    )

    monkeypatch.setenv("SEPAY_PG_ENV", "  PRODUCTION ")
    get_settings.cache_clear()
    prod_client = SePayClient()
    prod_form = prod_client.build_checkout_form(
        order_code="DH1001",
        amount_vnd=640000,
        description="Thanh toan don hang DH1001",
    )
    assert prod_form["checkout_url"] == "https://pay.sepay.vn/v1/checkout/init"
    assert prod_form["fields"]["signature"] == prod_client.sign_checkout_fields(prod_form["fields"])


# ------------------------------------------------------------------------------
# 2. POST /api/v1/erp/orders/{order_id}/sepay-checkout
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sepay_checkout_endpoint_auth_401_and_404_isolation(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
    tenant_b_headers: dict[str, str],
) -> None:
    """Verify 401 unauthenticated, 404 unknown UUID, and 404 cross-tenant isolation."""
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)
    monkeypatch.setattr(sepay_client, "pg_secret_key", TEST_PG_SECRET_KEY)
    monkeypatch.setattr(sepay_client, "pg_env", "sandbox")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated -> 401
        unauth_resp = await client.post(f"/api/v1/erp/orders/{SEEDED_ORDER_ID}/sepay-checkout")
        assert unauth_resp.status_code == 401

        bad_token_resp = await client.post(
            f"/api/v1/erp/orders/{SEEDED_ORDER_ID}/sepay-checkout",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert bad_token_resp.status_code == 401

        # 2. Unknown order UUID -> 404
        unknown_resp = await client.post(
            f"/api/v1/erp/orders/{uuid4()}/sepay-checkout",
            headers=auth_headers,
        )
        assert unknown_resp.status_code == 404

        # 3. Cross-tenant order access (tenant-b requesting default-tenant DH1001) -> 404
        cross_tenant_resp = await client.post(
            f"/api/v1/erp/orders/{SEEDED_ORDER_ID}/sepay-checkout",
            headers=tenant_b_headers,
        )
        assert cross_tenant_resp.status_code == 404


@pytest.mark.asyncio
async def test_sepay_checkout_endpoint_503_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Verify 503 when merchant ID or secret key is unconfigured."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Both empty -> 503
        monkeypatch.setattr(sepay_client, "pg_merchant_id", "")
        monkeypatch.setattr(sepay_client, "pg_secret_key", "")
        resp_both = await client.post(
            f"/api/v1/erp/orders/{SEEDED_ORDER_ID}/sepay-checkout",
            headers=auth_headers,
        )
        assert resp_both.status_code == 503

        # Missing secret key -> 503
        monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)
        monkeypatch.setattr(sepay_client, "pg_secret_key", "   ")
        resp_no_secret = await client.post(
            f"/api/v1/erp/orders/{SEEDED_ORDER_ID}/sepay-checkout",
            headers=auth_headers,
        )
        assert resp_no_secret.status_code == 503

        # Missing merchant ID -> 503
        monkeypatch.setattr(sepay_client, "pg_merchant_id", "")
        monkeypatch.setattr(sepay_client, "pg_secret_key", TEST_PG_SECRET_KEY)
        resp_no_merchant = await client.post(
            f"/api/v1/erp/orders/{SEEDED_ORDER_ID}/sepay-checkout",
            headers=auth_headers,
        )
        assert resp_no_merchant.status_code == 503


@pytest.mark.asyncio
async def test_sepay_checkout_endpoint_409_on_non_pending_order(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Verify 409 conflict when creating checkout for PAID, FULFILLING, COMPLETED, or CANCELLED order."""
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)
    monkeypatch.setattr(sepay_client, "pg_secret_key", TEST_PG_SECRET_KEY)
    monkeypatch.setattr(sepay_client, "pg_env", "sandbox")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for non_pending_status in (
            OrderStatus.PAID,
            OrderStatus.FULFILLING,
            OrderStatus.COMPLETED,
            OrderStatus.CANCELLED,
        ):
            updated = erp_repository.update_order_status(
                SEEDED_ORDER_ID,
                non_pending_status,
                tenant_id="default-tenant",
            )
            assert updated is not None
            assert updated.status == non_pending_status

            resp = await client.post(
                f"/api/v1/erp/orders/{SEEDED_ORDER_ID}/sepay-checkout",
                headers=auth_headers,
            )
            assert resp.status_code == 409


@pytest.mark.asyncio
async def test_sepay_checkout_endpoint_200_signed_response_without_secret_leak(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Verify 200 signed checkout response matches HMAC signature and never leaks pg_secret_key."""
    secret_marker = "super-sensitive-sepay-pg-secret-do-not-leak-999"
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)
    monkeypatch.setattr(sepay_client, "pg_secret_key", secret_marker)
    monkeypatch.setattr(sepay_client, "pg_env", "production")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/erp/orders/{SEEDED_ORDER_ID}/sepay-checkout",
            json={
                "payment_method": "BANK_TRANSFER",
                "success_url": "https://frontend.example/orders/success",
                "error_url": "https://frontend.example/orders/error",
                "cancel_url": "https://frontend.example/orders/cancel",
                "lang": "vi",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert secret_marker not in resp.text

        body = resp.json()
        assert body["checkout_url"] == "https://pay.sepay.vn/v1/checkout/init"
        assert body["order_id"] == str(SEEDED_ORDER_ID)
        assert body["order_code"] == "DH1001"
        assert body["total_amount_vnd"] == 640000

        fields = body["fields"]
        assert fields["order_amount"] == "640000"
        assert fields["merchant"] == TEST_PG_MERCHANT_ID
        assert fields["currency"] == "VND"
        assert fields["operation"] == "PURCHASE"
        assert fields["order_invoice_number"] == "DH1001"
        assert fields["customer_id"] == str(SEEDED_CUSTOMER_ID)
        assert fields["payment_method"] == "BANK_TRANSFER"
        assert fields["success_url"] == "https://frontend.example/orders/success"
        assert fields["error_url"] == "https://frontend.example/orders/error"
        assert fields["cancel_url"] == "https://frontend.example/orders/cancel"
        assert fields["lang"] == "vi"
        assert "signature" in fields
        assert fields["signature"] == sepay_client.sign_checkout_fields(fields)

        for val in fields.values():
            assert secret_marker not in str(val)


# ------------------------------------------------------------------------------
# 3. POST /api/v1/webhooks/sepay Payment Gateway IPN
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_ipn_fail_closed_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify fail-closed 401 when pg_ipn_secret is empty or X-Secret-Key is missing/wrong."""
    payload = _build_gateway_ipn_payload()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Empty sepay_pg_ipn_secret must fail closed even when caller sends X-Secret-Key
        monkeypatch.setattr(sepay_client, "pg_ipn_secret", "")
        resp_empty_cfg = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": TEST_PG_IPN_SECRET},
        )
        assert resp_empty_cfg.status_code == 401

        # 2. Missing X-Secret-Key header with configured pg_ipn_secret -> 401
        monkeypatch.setattr(sepay_client, "pg_ipn_secret", TEST_PG_IPN_SECRET)
        resp_missing_hdr = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
        )
        assert resp_missing_hdr.status_code == 401

        # 3. Wrong X-Secret-Key -> 401
        resp_wrong_hdr = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": "wrong-ipn-secret"},
        )
        assert resp_wrong_hdr.status_code == 401

        # 4. Whitespace-only X-Secret-Key -> 401
        resp_blank_hdr = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": "   "},
        )
        assert resp_blank_hdr.status_code == 401

    order = erp_repository.get_order_by_code("DH1001", tenant_id="default-tenant")
    assert order is not None
    assert order.status == OrderStatus.PENDING_PAYMENT
    assert await erp_repository.list_webhook_logs(tenant_id="default-tenant") == []
    assert erp_repository._outbox_jobs == {}


@pytest.mark.asyncio
async def test_gateway_ipn_order_paid_captured_reconciles_audit_and_telegram_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ORDER_PAID (CAPTURED) reconciles PENDING_PAYMENT->PAID, logs audit, and completes outbox."""
    monkeypatch.setattr(sepay_client, "pg_ipn_secret", TEST_PG_IPN_SECRET)
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)

    payload = _build_gateway_ipn_payload(
        notification_type="ORDER_PAID",
        order_status="CAPTURED",
        transaction_id="PG-TX-CAPTURED-001",
        transaction_status="APPROVED",
        transaction_amount="640000",
        transaction_currency="VND",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": TEST_PG_IPN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["idempotent_duplicate"] is False
        assert body["notification_type"] == "ORDER_PAID"
        assert body["order_code"] == "DH1001"
        assert body["order_status"] == "PAID"
        assert body["sepay_transaction_id"] == "PG-TX-CAPTURED-001"

        tg = body["telegram_dispatch"]
        assert tg is not None
        assert tg["dispatched"] is True
        assert tg["queued"] is True
        assert tg["order_code"] == "DH1001"
        job_ids = tg["outbox_job_ids"]
        assert len(job_ids) >= 1

    # Verify order persisted as PAID
    order = erp_repository.get_order_by_code("DH1001", tenant_id="default-tenant")
    assert order is not None
    assert order.status == OrderStatus.PAID
    assert order.sepay_transaction_id == "PG-TX-CAPTURED-001"

    # Verify audit log entry
    logs = await erp_repository.list_webhook_logs(tenant_id="default-tenant")
    assert len(logs) == 1
    assert logs[0].sepay_transaction_id == "PG-TX-CAPTURED-001"
    assert logs[0].order_code == "DH1001"
    assert logs[0].transfer_type == "pg_ipn"
    assert logs[0].transfer_amount == 640000
    assert logs[0].processing_status == "PROCESSED"

    # Verify telegram_dispatch outbox job completed
    matching_jobs = [
        job for job in erp_repository._outbox_jobs.values() if str(job["id"]) in job_ids
    ]
    assert len(matching_jobs) == len(job_ids)
    assert matching_jobs[0]["job_type"] == "telegram_dispatch"
    assert matching_jobs[0]["status"] == "COMPLETED"
    assert matching_jobs[0]["payload"]["order_code"] == "DH1001"


@pytest.mark.asyncio
async def test_gateway_ipn_order_paid_approved_reconciles_to_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ORDER_PAID with transaction_status=APPROVED reconciles even if order_status is not CAPTURED."""
    monkeypatch.setattr(sepay_client, "pg_ipn_secret", TEST_PG_IPN_SECRET)
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)

    payload = _build_gateway_ipn_payload(
        notification_type="ORDER_PAID",
        order_status="OPEN",
        transaction_id="PG-TX-APPROVED-002",
        transaction_status="APPROVED",
        transaction_amount="640000",
        transaction_currency="VND",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": TEST_PG_IPN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["order_status"] == "PAID"
        assert body["telegram_dispatch"]["dispatched"] is True

    order = erp_repository.get_order_by_code("DH1001", tenant_id="default-tenant")
    assert order is not None
    assert order.status == OrderStatus.PAID
    assert any(
        job["job_type"] == "telegram_dispatch" and job["status"] == "COMPLETED"
        for job in erp_repository._outbox_jobs.values()
    )


@pytest.mark.asyncio
async def test_gateway_ipn_idempotent_duplicate_replay_no_telegram_redispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify duplicate ORDER_PAID replay returns idempotent response and does NOT re-dispatch Telegram."""
    monkeypatch.setattr(sepay_client, "pg_ipn_secret", TEST_PG_IPN_SECRET)
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)

    dispatch_calls: list[dict[str, Any]] = []
    original_dispatch = telegram_dispatch_client.dispatch_order_alert

    async def _spy_dispatch_order_alert(*args: Any, **kwargs: Any) -> dict[str, Any]:
        dispatch_calls.append({"args": args, "kwargs": kwargs})
        return await original_dispatch(*args, **kwargs)

    monkeypatch.setattr(
        telegram_dispatch_client,
        "dispatch_order_alert",
        _spy_dispatch_order_alert,
    )

    payload = _build_gateway_ipn_payload(
        notification_type="ORDER_PAID",
        order_status="CAPTURED",
        transaction_id="PG-TX-IDEMPOTENT-003",
        transaction_status="APPROVED",
        transaction_amount="640000",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_resp = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": TEST_PG_IPN_SECRET},
        )
        assert first_resp.status_code == 200
        first_body = first_resp.json()
        assert first_body["idempotent_duplicate"] is False
        assert first_body["order_status"] == "PAID"
        assert len(dispatch_calls) == 1
        outbox_count_after_first = len(erp_repository._outbox_jobs)
        assert outbox_count_after_first == 1

        # Replay identical IPN
        replay_resp = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": TEST_PG_IPN_SECRET},
        )
        assert replay_resp.status_code == 200
        replay_body = replay_resp.json()
        assert replay_body["success"] is True
        assert replay_body["idempotent_duplicate"] is True
        assert replay_body["order_code"] == "DH1001"
        assert replay_body["order_status"] == "PAID"
        assert replay_body["sepay_transaction_id"] == "PG-TX-IDEMPOTENT-003"

        # Telegram must NOT have been re-dispatched and no new outbox job enqueued
        assert len(dispatch_calls) == 1
        assert len(erp_repository._outbox_jobs) == outbox_count_after_first


@pytest.mark.asyncio
async def test_gateway_ipn_underpaid_does_not_mark_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify underpaid ORDER_PAID IPN logs UNDERPAID audit without marking order PAID or dispatching."""
    monkeypatch.setattr(sepay_client, "pg_ipn_secret", TEST_PG_IPN_SECRET)
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)

    payload = _build_gateway_ipn_payload(
        notification_type="ORDER_PAID",
        order_status="CAPTURED",
        transaction_id="PG-TX-UNDERPAID-004",
        transaction_status="APPROVED",
        order_amount="320000",
        transaction_amount="320000",
        transaction_currency="VND",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": TEST_PG_IPN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["payment_verified"] is False
        assert body["reason"] == "underpayment"
        assert body["order_status"] == "PENDING_PAYMENT"
        assert body["expected_amount_vnd"] == 640000
        assert body["received_amount_vnd"] == 320000

    order = erp_repository.get_order_by_code("DH1001", tenant_id="default-tenant")
    assert order is not None
    assert order.status == OrderStatus.PENDING_PAYMENT

    logs = await erp_repository.list_webhook_logs(tenant_id="default-tenant")
    assert len(logs) == 1
    assert logs[0].processing_status == "UNDERPAID"
    assert erp_repository._outbox_jobs == {}


@pytest.mark.asyncio
async def test_gateway_ipn_non_vnd_currency_does_not_mark_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify non-VND currency ORDER_PAID IPN logs IGNORED_CURRENCY without marking order PAID."""
    monkeypatch.setattr(sepay_client, "pg_ipn_secret", TEST_PG_IPN_SECRET)
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)

    payload = _build_gateway_ipn_payload(
        notification_type="ORDER_PAID",
        order_status="CAPTURED",
        transaction_id="PG-TX-USD-005",
        transaction_status="APPROVED",
        order_currency="USD",
        transaction_currency="USD",
        transaction_amount="640000",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": TEST_PG_IPN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["ignored"] is True
        assert body["reason"] == "currency_mismatch"

    order = erp_repository.get_order_by_code("DH1001", tenant_id="default-tenant")
    assert order is not None
    assert order.status == OrderStatus.PENDING_PAYMENT

    logs = await erp_repository.list_webhook_logs(tenant_id="default-tenant")
    assert len(logs) == 1
    assert logs[0].processing_status == "IGNORED_CURRENCY"
    assert erp_repository._outbox_jobs == {}


@pytest.mark.asyncio
async def test_gateway_ipn_transaction_void_logs_audit_without_marking_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify TRANSACTION_VOID returns 200 and records audit log without marking order PAID."""
    monkeypatch.setattr(sepay_client, "pg_ipn_secret", TEST_PG_IPN_SECRET)
    monkeypatch.setattr(sepay_client, "pg_merchant_id", TEST_PG_MERCHANT_ID)

    payload = _build_gateway_ipn_payload(
        notification_type="TRANSACTION_VOID",
        order_status="CANCELLED",
        transaction_id="PG-TX-VOID-006",
        transaction_status="VOID",
        transaction_amount="640000",
        transaction_currency="VND",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/sepay",
            json=payload,
            headers={"X-Secret-Key": TEST_PG_IPN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["idempotent_duplicate"] is False
        assert body["ignored"] is True
        assert body["reason"] == "notification_transaction_void"
        assert body["notification_type"] == "TRANSACTION_VOID"
        assert body["order_code"] == "DH1001"
        assert body["sepay_transaction_id"] == "PG-TX-VOID-006"

    order = erp_repository.get_order_by_code("DH1001", tenant_id="default-tenant")
    assert order is not None
    assert order.status == OrderStatus.PENDING_PAYMENT

    logs = await erp_repository.list_webhook_logs(tenant_id="default-tenant")
    assert len(logs) == 1
    assert logs[0].sepay_transaction_id == "PG-TX-VOID-006"
    assert logs[0].transfer_type == "pg_ipn"
    assert logs[0].processing_status == "IGNORED_TRANSACTION_VOID"
    assert erp_repository._outbox_jobs == {}


# ------------------------------------------------------------------------------
# 4. render.yaml & .env.example Configuration & Secret Hygiene
# ------------------------------------------------------------------------------


def test_render_yaml_and_env_example_sepay_pg_config_and_no_committed_secrets() -> None:
    """Verify render.yaml and .env.example declare SEPAY_PG_* vars safely with zero real/test secrets."""
    render_text = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
    env_example_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    required_pg_keys = (
        "SEPAY_PG_MERCHANT_ID",
        "SEPAY_PG_SECRET_KEY",
        "SEPAY_PG_IPN_SECRET",
        "SEPAY_PG_ENV",
    )
    for key in required_pg_keys:
        assert re.search(rf"key:\s*{key}\b", render_text) is not None, (
            f"{key} missing from render.yaml"
        )
        assert re.search(rf"^{key}=", env_example_text, flags=re.MULTILINE) is not None, (
            f"{key} missing from .env.example"
        )

    for secret_key in ("SEPAY_PG_MERCHANT_ID", "SEPAY_PG_SECRET_KEY", "SEPAY_PG_IPN_SECRET"):
        assert re.search(rf"key:\s*{secret_key}\s*\n\s*sync:\s*false\b", render_text) is not None, (
            f"{secret_key} in render.yaml must use sync: false"
        )

    assert re.search(r"key:\s*SEPAY_PG_ENV\s*\n\s*value:\s*sandbox\b", render_text) is not None
    assert re.search(r"^SEPAY_PG_ENV=sandbox\b", env_example_text, flags=re.MULTILINE) is not None

    # Ensure zero real or test `spsk_` Payment Gateway secret keys are committed in config or app source.
    forbidden_prefix = "spsk" + "_"
    assert forbidden_prefix not in render_text
    assert forbidden_prefix not in env_example_text
    for py_file in (REPO_ROOT / "backend" / "app").rglob("*.py"):
        assert forbidden_prefix not in py_file.read_text(encoding="utf-8"), (
            f"Forbidden PG secret prefix found in {py_file}"
        )
