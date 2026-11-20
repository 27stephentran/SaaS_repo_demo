"""Phase 1 P0 security, auth, tenant isolation, inventory invariant, and webhook regression tests."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.core.auth import create_access_token, create_refresh_token, decode_and_verify_token
from app.core.config import Settings, get_settings
from app.core.database import DEFAULT_LOCAL_DATABASE_URL, erp_repository
from app.main import app
from app.models.entities import EmployeeResponse, OrderStatus
from app.services.integrations import (
    SePayClient,
    meta_graph_client,
    sepay_client,
    telegram_dispatch_client,
)
from app.services.order_service import order_service


def _decode_jwt_segment(segment: str) -> dict[str, Any]:
    padding = "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode((segment + padding).encode("ascii"))
    return dict(json.loads(raw.decode("utf-8")))


# ------------------------------------------------------------------------------
# 1. Config & Fail-Closed Production Guards
# ------------------------------------------------------------------------------


def test_production_config_fails_closed_on_weak_secret_or_invalid_cors() -> None:
    """`APP_ENV=production` rejects empty/short API_TOKEN_SECRET and empty or wildcard CORS_ORIGINS."""
    valid_secret = "prod-api-token-secret-at-least-32-bytes-long"

    with pytest.raises(ValidationError, match="API_TOKEN_SECRET"):
        Settings(
            app_env="production",
            api_token_secret="",
            cors_origins=["https://saa-s-repo-demo.vercel.app"],
        )

    with pytest.raises(ValidationError, match="API_TOKEN_SECRET"):
        Settings(
            app_env="production",
            api_token_secret="too-short-secret",
            cors_origins=["https://saa-s-repo-demo.vercel.app"],
        )

    with pytest.raises(ValidationError, match="wildcard"):
        Settings(
            app_env="production",
            api_token_secret=valid_secret,
            cors_origins=["*"],
        )

    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(
            app_env="production",
            api_token_secret=valid_secret,
            cors_origins=[],
        )

    prod_settings = Settings(
        app_env="production",
        api_token_secret=valid_secret,
        cors_origins=["https://saa-s-repo-demo.vercel.app"],
    )
    assert prod_settings.app_env == "production"
    assert prod_settings.api_token_secret == valid_secret
    assert prod_settings.cors_origins == ["https://saa-s-repo-demo.vercel.app"]


@pytest.mark.asyncio
async def test_is_postgres_active_fails_closed_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`is_postgres_active()` raises RuntimeError in production when DB is default or unreachable."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", DEFAULT_LOCAL_DATABASE_URL)

    with pytest.raises(RuntimeError, match="PostgreSQL DATABASE_URL"):
        await erp_repository.is_postgres_active()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:1/unreachable_db",
    )

    async def _fail_session_execute(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionRefusedError("DB unreachable")

    class _FailingSessionCtx:
        async def __aenter__(self) -> _FailingSessionCtx:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionRefusedError("DB unreachable")

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", _FailingSessionCtx)

    with pytest.raises(RuntimeError, match="unavailable in production"):
        await erp_repository.is_postgres_active()


# ------------------------------------------------------------------------------
# 2. Auth, Access Tokens, Revocation, Refresh Rotation & Rate Limiting
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_token_issue_claims_and_me_endpoint(
    create_test_user: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
    """`POST /api/v1/auth/token` issues HS256 JWTs with iss/aud/jti/tenant_id; `/me` returns claims."""
    user = await create_test_user(
        email="operator@default-tenant.test",
        password="OperatorSecret!999",
        role="manager",
        tenant_id="default-tenant",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bad_pw_resp = await client.post(
            "/api/v1/auth/token",
            json={
                "email": "operator@default-tenant.test",
                "password": "wrong-password",
                "tenant_id": "default-tenant",
            },
        )
        assert bad_pw_resp.status_code == 401

        token_resp = await client.post(
            "/api/v1/auth/token",
            json={
                "email": "operator@default-tenant.test",
                "password": "OperatorSecret!999",
                "tenant_id": "default-tenant",
            },
        )
        assert token_resp.status_code == 200
        pair = token_resp.json()
        assert pair["token_type"] == "bearer"
        assert pair["tenant_id"] == "default-tenant"
        assert pair["role"] == "manager"

        access_token = pair["access_token"]
        refresh_token = pair["refresh_token"]

        header = _decode_jwt_segment(access_token.split(".")[0])
        assert header["alg"] == "HS256"

        access_claims = await decode_and_verify_token(access_token, expected_type="access")
        refresh_claims = await decode_and_verify_token(refresh_token, expected_type="refresh")

        settings = get_settings()
        for claims in (access_claims, refresh_claims):
            assert claims["iss"] == settings.api_token_issuer
            assert claims["aud"] == settings.api_token_audience
            assert claims["tenant_id"] == "default-tenant"
            assert claims["sub"] == str(user["id"])
            assert isinstance(claims["jti"], str) and claims["jti"]

        unauth_me = await client.get("/api/v1/auth/me")
        assert unauth_me.status_code == 401

        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["user_id"] == str(user["id"])
        assert me_data["email"] == "operator@default-tenant.test"
        assert me_data["tenant_id"] == "default-tenant"
        assert me_data["role"] == "manager"


@pytest.mark.asyncio
async def test_erp_endpoints_reject_missing_tampered_expired_and_cross_tenant(
    auth_headers: dict[str, str],
    tenant_b_headers: dict[str, str],
) -> None:
    """`/api/v1/erp/*` fails closed on missing/tampered/expired tokens (401) and cross-tenant overrides (403)."""
    customers = erp_repository.list_customers("default-tenant")
    seeded_order = erp_repository.list_orders("default-tenant")[0]

    expired_token = create_access_token(
        user_id=uuid4(),
        tenant_id="default-tenant",
        role="admin",
        email="expired@default-tenant.test",
        ttl_seconds=-30,
    )
    valid_token = auth_headers["Authorization"].split(" ", 1)[1]
    tampered_token = valid_token[:-4] + ("aaaa" if not valid_token.endswith("aaaa") else "bbbb")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing token -> 401 across ERP endpoints
        for method, path in (
            ("GET", "/api/v1/erp/inventory"),
            ("POST", "/api/v1/erp/inventory/search"),
            ("GET", "/api/v1/erp/orders"),
            ("POST", "/api/v1/erp/orders"),
            ("GET", "/api/v1/erp/customers"),
            ("GET", "/api/v1/erp/employees"),
            ("PATCH", f"/api/v1/erp/orders/{seeded_order.id}/status"),
        ):
            resp = await client.request(method, path)
            assert resp.status_code == 401

        # 2. Tampered and expired tokens -> 401
        for bad_token in (tampered_token, expired_token):
            bad_resp = await client.get(
                "/api/v1/erp/inventory",
                headers={"Authorization": f"Bearer {bad_token}"},
            )
            assert bad_resp.status_code == 401

        # 3. Cross-tenant override attempts via query, header, and body -> 403
        query_override = await client.get(
            "/api/v1/erp/inventory",
            params={"tenant_id": "tenant-b"},
            headers=auth_headers,
        )
        assert query_override.status_code == 403

        header_override = await client.get(
            "/api/v1/erp/orders",
            headers={**auth_headers, "X-Tenant-Id": "tenant-b"},
        )
        assert header_override.status_code == 403

        body_override = await client.post(
            "/api/v1/erp/orders",
            json={
                "tenant_id": "tenant-b",
                "customer_id": str(customers[0].id),
                "items": [{"sku": "COFFEE-ROBUSTA-1KG", "quantity": 1}],
            },
            headers=auth_headers,
        )
        assert body_override.status_code == 403

        # 4. Tenant B user cannot see or mutate default-tenant orders
        tenant_b_orders = await client.get("/api/v1/erp/orders", headers=tenant_b_headers)
        assert tenant_b_orders.status_code == 200
        assert tenant_b_orders.json() == []

        tenant_b_patch = await client.patch(
            f"/api/v1/erp/orders/{seeded_order.id}/status",
            json={"status": "PAID"},
            headers=tenant_b_headers,
        )
        assert tenant_b_patch.status_code == 404


@pytest.mark.asyncio
async def test_auth_logout_revokes_jti_and_refresh_rotates_with_replay_block(
    create_test_user: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
    """`POST /api/v1/auth/logout` revokes JTI; `POST /api/v1/auth/refresh` rotates & blocks replay."""
    await create_test_user(
        email="session@default-tenant.test",
        password="SessionPass!123",
        role="staff",
        tenant_id="default-tenant",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Login #1: test refresh rotation & old refresh replay rejection
        login_1 = await client.post(
            "/api/v1/auth/token",
            json={
                "email": "session@default-tenant.test",
                "password": "SessionPass!123",
                "tenant_id": "default-tenant",
            },
        )
        assert login_1.status_code == 200
        tokens_1 = login_1.json()

        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens_1["refresh_token"]},
        )
        assert refresh_resp.status_code == 200
        rotated = refresh_resp.json()
        assert rotated["refresh_token"] != tokens_1["refresh_token"]
        assert rotated["access_token"] != tokens_1["access_token"]

        # Replay of old refresh token must fail with 401
        replay_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens_1["refresh_token"]},
        )
        assert replay_resp.status_code == 401

        # Rotated access token works before logout
        me_before_logout = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {rotated['access_token']}"},
        )
        assert me_before_logout.status_code == 200

        # Logout revokes access & refresh JTIs
        logout_resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {rotated['access_token']}"},
            json={"refresh_token": rotated["refresh_token"]},
        )
        assert logout_resp.status_code == 200
        assert logout_resp.json()["revoked"] is True

        # Subsequent request with revoked access token -> 401
        after_logout_me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {rotated['access_token']}"},
        )
        assert after_logout_me.status_code == 401

        after_logout_erp = await client.get(
            "/api/v1/erp/inventory",
            headers={"Authorization": f"Bearer {rotated['access_token']}"},
        )
        assert after_logout_erp.status_code == 401

        after_logout_refresh = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": rotated["refresh_token"]},
        )
        assert after_logout_refresh.status_code == 401


@pytest.mark.asyncio
async def test_auth_token_rate_limiting_returns_429(
    monkeypatch: pytest.MonkeyPatch,
    create_test_user: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
    """`POST /api/v1/auth/token` returns 429 Too Many Requests when exceeding configured limit."""
    await create_test_user(
        email="ratelimit@default-tenant.test",
        password="RateLimitPass!123",
        tenant_id="default-tenant",
    )
    monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            resp = await client.post(
                "/api/v1/auth/token",
                json={
                    "email": "ratelimit@default-tenant.test",
                    "password": "wrong-password",
                    "tenant_id": "default-tenant",
                },
            )
            assert resp.status_code == 401

        blocked_resp = await client.post(
            "/api/v1/auth/token",
            json={
                "email": "ratelimit@default-tenant.test",
                "password": "RateLimitPass!123",
                "tenant_id": "default-tenant",
            },
        )
        assert blocked_resp.status_code == 429
        assert int(blocked_resp.headers["retry-after"]) >= 1


# ------------------------------------------------------------------------------
# 3. CORS Enforcement
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cors_preflight_allows_trusted_and_rejects_untrusted_origins() -> None:
    """CORS preflight allows configured Vercel/localhost origins and omits untrusted origins."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for allowed_origin in ("https://saa-s-repo-demo.vercel.app", "http://localhost:3000"):
            resp = await client.options(
                "/api/v1/erp/inventory",
                headers={
                    "Origin": allowed_origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization,Content-Type",
                },
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == allowed_origin
            assert resp.headers.get("access-control-allow-credentials") == "true"

        evil_resp = await client.options(
            "/api/v1/erp/inventory",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert "access-control-allow-origin" not in evil_resp.headers


# ------------------------------------------------------------------------------
# 4. Server-Authoritative Orders, Atomic Stock & State Machine
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_authoritative_pricing_atomic_stock_and_rollback(
    auth_headers: dict[str, str],
) -> None:
    """Client price forgery is ignored; stock decrements atomically and rejects oversell with 409."""
    customer = erp_repository.list_customers("default-tenant")[0]
    robusta = next(
        item
        for item in erp_repository.list_inventory("default-tenant")
        if item.sku == "COFFEE-ROBUSTA-1KG"
    )
    v60 = next(
        item
        for item in erp_repository.list_inventory("default-tenant")
        if item.sku == "EQUIP-V60-KIT"
    )
    initial_robusta_stock = robusta.stock_quantity
    initial_v60_stock = v60.stock_quantity

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Client sends forged unit_price_vnd=1 -> server computes catalog price (320,000 * 2)
        order_resp = await client.post(
            "/api/v1/erp/orders",
            json={
                "customer_id": str(customer.id),
                "items": [
                    {
                        "sku": "COFFEE-ROBUSTA-1KG",
                        "quantity": 2,
                        "unit_price_vnd": 1,
                        "name": "Forged Product Name",
                    }
                ],
            },
            headers=auth_headers,
        )
        assert order_resp.status_code == 201
        order = order_resp.json()
        assert order["total_amount_vnd"] == robusta.price_vnd * 2
        assert order["items"][0]["unit_price_vnd"] == robusta.price_vnd
        assert order["items"][0]["name"] == robusta.name
        assert f"amount={robusta.price_vnd * 2}" in order["sepay_qr_url"]

        after_order_robusta = next(
            item
            for item in erp_repository.list_inventory("default-tenant")
            if item.sku == "COFFEE-ROBUSTA-1KG"
        )
        assert after_order_robusta.stock_quantity == initial_robusta_stock - 2

        # 2. Oversell attempt (single or multi-SKU atomic check) -> 409 without negative/partial stock
        oversell_resp = await client.post(
            "/api/v1/erp/orders",
            json={
                "customer_id": str(customer.id),
                "items": [
                    {"sku": "EQUIP-V60-KIT", "quantity": 2},
                    {"sku": "COFFEE-ROBUSTA-1KG", "quantity": initial_robusta_stock},
                ],
            },
            headers=auth_headers,
        )
        assert oversell_resp.status_code == 409

        robusta_after_fail = next(
            item
            for item in erp_repository.list_inventory("default-tenant")
            if item.sku == "COFFEE-ROBUSTA-1KG"
        )
        v60_after_fail = next(
            item
            for item in erp_repository.list_inventory("default-tenant")
            if item.sku == "EQUIP-V60-KIT"
        )
        assert robusta_after_fail.stock_quantity == initial_robusta_stock - 2
        assert robusta_after_fail.stock_quantity >= 0
        assert v60_after_fail.stock_quantity == initial_v60_stock


@pytest.mark.asyncio
async def test_order_cancellation_and_stale_expiration_restore_stock_and_enforce_state_machine(
    auth_headers: dict[str, str],
) -> None:
    """CANCELLED transition and stale PENDING_PAYMENT expiry restore stock; invalid transitions return 409."""
    customer = erp_repository.list_customers("default-tenant")[0]
    arabica = next(
        item
        for item in erp_repository.list_inventory("default-tenant")
        if item.sku == "COFFEE-ARABICA-500G"
    )
    starting_stock = arabica.stock_quantity

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create order (decrements 4 units) and test invalid transitions + CANCELLED restock
        create_1 = await client.post(
            "/api/v1/erp/orders",
            json={
                "customer_id": str(customer.id),
                "items": [{"sku": "COFFEE-ARABICA-500G", "quantity": 4}],
            },
            headers=auth_headers,
        )
        assert create_1.status_code == 201
        order_1_id = create_1.json()["id"]

        # PENDING_PAYMENT -> FULFILLING must fail with 409
        invalid_1 = await client.patch(
            f"/api/v1/erp/orders/{order_1_id}/status",
            json={"status": "FULFILLING"},
            headers=auth_headers,
        )
        assert invalid_1.status_code == 409

        # Cancel order -> 200 and stock restored
        cancel_resp = await client.patch(
            f"/api/v1/erp/orders/{order_1_id}/status",
            json={"status": "CANCELLED"},
            headers=auth_headers,
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "CANCELLED"

        restored = next(
            item
            for item in erp_repository.list_inventory("default-tenant")
            if item.sku == "COFFEE-ARABICA-500G"
        )
        assert restored.stock_quantity == starting_stock

        # CANCELLED -> PAID must fail with 409
        cancelled_to_paid = await client.patch(
            f"/api/v1/erp/orders/{order_1_id}/status",
            json={"status": "PAID"},
            headers=auth_headers,
        )
        assert cancelled_to_paid.status_code == 409

        # 2. Complete an order and verify COMPLETED -> CANCELLED returns 409
        create_2 = await client.post(
            "/api/v1/erp/orders",
            json={
                "customer_id": str(customer.id),
                "items": [{"sku": "COFFEE-ARABICA-500G", "quantity": 2}],
            },
            headers=auth_headers,
        )
        assert create_2.status_code == 201
        order_2_id = create_2.json()["id"]

        for step in ("PAID", "FULFILLING", "COMPLETED"):
            step_resp = await client.patch(
                f"/api/v1/erp/orders/{order_2_id}/status",
                json={"status": step},
                headers=auth_headers,
            )
            assert step_resp.status_code == 200

        completed_to_cancelled = await client.patch(
            f"/api/v1/erp/orders/{order_2_id}/status",
            json={"status": "CANCELLED"},
            headers=auth_headers,
        )
        assert completed_to_cancelled.status_code == 409

        # 3. Stale PENDING_PAYMENT expiration restores reserved stock
        create_3 = await client.post(
            "/api/v1/erp/orders",
            json={
                "customer_id": str(customer.id),
                "items": [{"sku": "COFFEE-ARABICA-500G", "quantity": 5}],
            },
            headers=auth_headers,
        )
        assert create_3.status_code == 201
        order_3_id = create_3.json()["id"]

        stock_while_pending = next(
            item
            for item in erp_repository.list_inventory("default-tenant")
            if item.sku == "COFFEE-ARABICA-500G"
        ).stock_quantity
        assert stock_while_pending == starting_stock - 2 - 5

        # Backdate order_3 beyond order_payment_ttl_seconds (1800s)
        stored_order_3 = next(
            o for o in erp_repository.list_orders("default-tenant") if str(o.id) == order_3_id
        )
        erp_repository._orders[stored_order_3.id] = stored_order_3.model_copy(
            update={"created_at": datetime.now(UTC) - timedelta(seconds=3600)}
        )

        list_after_expiry = await client.get("/api/v1/erp/orders", headers=auth_headers)
        assert list_after_expiry.status_code == 200
        expired_record = next(
            o for o in list_after_expiry.json() if o["id"] == order_3_id
        )
        assert expired_record["status"] == OrderStatus.CANCELLED.value

        stock_after_expiry = next(
            item
            for item in erp_repository.list_inventory("default-tenant")
            if item.sku == "COFFEE-ARABICA-500G"
        ).stock_quantity
        assert stock_after_expiry == starting_stock - 2


# ------------------------------------------------------------------------------
# 5. Meta & SePay Hardening (Signatures, Dedup, Tenant Channels, Spacing, Outbox)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meta_webhook_signature_enforcement_and_deduplication(
    monkeypatch: pytest.MonkeyPatch,
    meta_signature_headers: Callable[[bytes, str | None], dict[str, str]],
) -> None:
    """`POST /api/v1/webhooks/meta` rejects missing/invalid signatures (401) and deduplicates mid & comment_id."""
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "page_123",
                "messaging": [
                    {
                        "sender": {"id": "psid_dedup_user"},
                        "recipient": {"id": "page_123"},
                        "message": {
                            "mid": "mid_dedup_001",
                            "text": "Cho mình mua 1 gói Arabica Cau Dat",
                        },
                    }
                ],
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "item": "comment",
                            "comment_id": "comment_dedup_001",
                            "message": "Báo giá cà phê Robusta giúp mình",
                        },
                    }
                ],
            }
        ],
    }
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing X-Hub-Signature-256 -> 401
        missing_sig_resp = await client.post(
            "/api/v1/webhooks/meta",
            content=raw_body,
            headers={"Content-Type": "application/json"},
        )
        assert missing_sig_resp.status_code == 401

        # 2. Invalid X-Hub-Signature-256 -> 401
        bad_sig_resp = await client.post(
            "/api/v1/webhooks/meta",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=deadbeef",
            },
        )
        assert bad_sig_resp.status_code == 401

        # 3. Empty meta_app_secret fails closed -> 401
        monkeypatch.setattr(meta_graph_client, "app_secret", "")
        no_secret_resp = await client.post(
            "/api/v1/webhooks/meta",
            content=raw_body,
            headers=meta_signature_headers(raw_body, "some-secret"),
        )
        assert no_secret_resp.status_code == 401

        # Restore valid app secret for signed delivery
        monkeypatch.setattr(meta_graph_client, "app_secret", "test-meta-app-secret-0123456789")
        valid_headers = meta_signature_headers(raw_body, None)

        # 4. First signed delivery processes both messaging and comment events
        first_resp = await client.post(
            "/api/v1/webhooks/meta",
            content=raw_body,
            headers=valid_headers,
        )
        assert first_resp.status_code == 200
        first_data = first_resp.json()
        assert first_data["processed_count"] == 2
        assert first_data["events"][0]["type"] == "messaging"
        assert first_data["events"][0]["mid"] == "mid_dedup_001"
        assert first_data["events"][0]["order_code"] is not None
        assert first_data["events"][1]["type"] == "private_reply"
        assert first_data["events"][1]["comment_id"] == "comment_dedup_001"

        # 5. Duplicate delivery of same mid and comment_id is deduplicated via record_webhook_dedup
        dup_resp = await client.post(
            "/api/v1/webhooks/meta",
            content=raw_body,
            headers=valid_headers,
        )
        assert dup_resp.status_code == 200
        dup_data = dup_resp.json()
        assert dup_data["processed_count"] == 2
        assert dup_data["events"][0]["status"] == "skipped_duplicate"
        assert dup_data["events"][1]["status"] == "skipped_duplicate"

        # Direct repository dedup helper check
        assert (
            await erp_repository.record_webhook_dedup(
                provider="meta",
                external_event_id="mid_dedup_001",
                payload_hash="hash-1",
                tenant_id="default-tenant",
            )
            is False
        )


@pytest.mark.asyncio
async def test_tenant_channels_resolve_meta_and_sepay_to_owning_tenant(
    monkeypatch: pytest.MonkeyPatch,
    meta_signature_headers: Callable[[bytes, str | None], dict[str, str]],
) -> None:
    """`tenant_channels` resolves Meta Page ID & SePay account to owning tenant and fails closed in prod."""
    erp_repository._tenant_channels[("meta_page", "page_tenant_b", "")] = {
        "tenant_id": "tenant-b",
        "is_active": True,
    }
    erp_repository._tenant_channels[("sepay_account", "999888777", "")] = {
        "tenant_id": "tenant-b",
        "is_active": True,
    }

    assert await erp_repository.resolve_channel_tenant("meta_page", "page_tenant_b") == "tenant-b"
    assert (
        await erp_repository.resolve_channel_tenant("sepay_account", "999888777") == "tenant-b"
    )
    assert await erp_repository.resolve_channel_tenant("meta_page", "unknown_page") is None

    # Seed an inventory item and order in tenant-b so webhook routing targets tenant-b
    await order_service.create_inventory_item(
        payload=await _build_tenant_b_inventory_payload(),
        embedding=[0.1] * 768,
        tenant_id="tenant-b",
    )
    tenant_b_customer = erp_repository.get_or_create_customer_by_psid(
        meta_psid="psid_tb_001",
        full_name="Tenant B Buyer",
        tenant_id="tenant-b",
    )
    tenant_b_order = await order_service.create_order(
        payload=await _build_tenant_b_order_payload(tenant_b_customer.id),
        tenant_id="tenant-b",
    )

    meta_payload = {
        "object": "page",
        "entry": [
            {
                "id": "page_tenant_b",
                "messaging": [
                    {
                        "sender": {"id": "psid_tb_002"},
                        "recipient": {"id": "page_tenant_b"},
                        "message": {"mid": "mid_tb_1", "text": "Cho mình mua Trà Matcha"},
                    }
                ],
            }
        ],
    }
    raw_meta = json.dumps(meta_payload, separators=(",", ":")).encode("utf-8")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        meta_resp = await client.post(
            "/api/v1/webhooks/meta",
            content=raw_meta,
            headers=meta_signature_headers(raw_meta, None),
        )
        assert meta_resp.status_code == 200
        meta_event = meta_resp.json()["events"][0]
        assert meta_event["tenant_id"] == "tenant-b"

        sepay_resp = await client.post(
            "/api/v1/webhooks/sepay",
            json={
                "id": 771001,
                "gateway": "MBBank",
                "transactionDate": "2026-11-20 08:00:00",
                "accountNumber": "999888777",
                "content": f"SEVQR {tenant_b_order.order_code}",
                "transferType": "in",
                "transferAmount": tenant_b_order.total_amount_vnd,
                "referenceCode": "FT_TB_001",
            },
            headers={"Authorization": f"Apikey {sepay_client.webhook_secret}"},
        )
        assert sepay_resp.status_code == 200
        sepay_data = sepay_resp.json()
        assert sepay_data["order_code"] == tenant_b_order.order_code
        assert sepay_data["order_status"] == "PAID"

        # In production mode, unregistered SePay channel fails closed with 403
        monkeypatch.setenv("APP_ENV", "production")

        async def _pg_inactive() -> bool:
            return False

        monkeypatch.setattr(erp_repository, "is_postgres_active", _pg_inactive)
        unreg_resp = await client.post(
            "/api/v1/webhooks/sepay",
            json={
                "id": 771002,
                "gateway": "MBBank",
                "transactionDate": "2026-11-20 08:05:00",
                "accountNumber": "000000000",
                "content": "DH1001",
                "transferType": "in",
                "transferAmount": 640000,
                "referenceCode": "FT_UNREG",
            },
            headers={"Authorization": f"Apikey {sepay_client.webhook_secret}"},
        )
        assert unreg_resp.status_code == 403


async def _build_tenant_b_inventory_payload() -> Any:
    from app.models.entities import InventoryCreateRequest

    return InventoryCreateRequest(
        tenant_id="tenant-b",
        sku="TB-MATCHA-50G",
        name="Trà Matcha Tenant B 50g",
        description="Ceremonial matcha dedicated to tenant B.",
        category="Tea",
        price_vnd=250000,
        stock_quantity=10,
    )


async def _build_tenant_b_order_payload(customer_id: Any) -> Any:
    from app.models.entities import OrderCreateRequest, OrderLineItemRequest

    return OrderCreateRequest(
        tenant_id="tenant-b",
        customer_id=customer_id,
        items=[OrderLineItemRequest(sku="TB-MATCHA-50G", quantity=1)],
    )


@pytest.mark.asyncio
async def test_sepay_memo_prefix_spacing_extraction_and_outbox_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SePay `memo_prefix` builds `'SEVQR DH1001'`, extracts `DH1001`, and enqueues `telegram_dispatch`."""
    monkeypatch.setenv("SEPAY_MEMO_PREFIX", "SEVQR")
    get_settings.cache_clear()
    prefixed_client = SePayClient()

    qr_url = prefixed_client.generate_vietqr_url(amount_vnd=640000, order_code="DH1001")
    assert "des=SEVQR%20DH1001" in qr_url

    assert prefixed_client.extract_order_code("Chuyen khoan SEVQR DH1001") == "DH1001"
    assert prefixed_client.extract_order_code("SEVQRDH1001") == "DH1001"
    assert prefixed_client.extract_order_code("chuyen tien", explicit_code="SEVQR DH1001") == "DH1001"

    monkeypatch.setattr(sepay_client, "memo_prefix", "SEVQR")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhooks/sepay",
            json={
                "id": 995501,
                "gateway": "MBBank",
                "transactionDate": "2026-11-20 09:00:00",
                "accountNumber": "0123456789",
                "content": "NGUYEN MINH ANH SEVQR DH1001",
                "transferType": "in",
                "transferAmount": 640000,
                "referenceCode": "FT_OUTBOX_001",
            },
            headers={"Authorization": f"Apikey {sepay_client.webhook_secret}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["order_code"] == "DH1001"
        assert body["order_status"] == "PAID"
        assert body["telegram_dispatch"]["dispatched"] is True
        job_ids = body["telegram_dispatch"]["outbox_job_ids"]
        assert len(job_ids) >= 1

        matching_jobs = [
            job
            for job in erp_repository._outbox_jobs.values()
            if str(job["id"]) in job_ids
        ]
        assert len(matching_jobs) == len(job_ids)
        assert matching_jobs[0]["job_type"] == "telegram_dispatch"
        assert matching_jobs[0]["tenant_id"] == "default-tenant"
        assert matching_jobs[0]["status"] == "COMPLETED"
        assert matching_jobs[0]["payload"]["order_code"] == "DH1001"


# ------------------------------------------------------------------------------
# 6. Phase 1 Rework Regression Tests (SEC-001, SEC-002, OBS-002)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sec001_meta_bearer_auth_and_no_secret_leak_on_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    meta_signature_headers: Callable[[bytes, str | None], dict[str, str]],
) -> None:
    """SEC-001: Meta uses Authorization Bearer header (not URL query param) and HTTP errors never leak secrets."""
    secret_meta_token = "EAAB_SUPER_SECRET_META_TOKEN_VALUE_XYZ"
    secret_tg_token = "987654321:SUPER_SECRET_TELEGRAM_BOT_TOKEN_XYZ"
    monkeypatch.setattr(meta_graph_client, "access_token", secret_meta_token)
    monkeypatch.setattr(telegram_dispatch_client, "bot_token", secret_tg_token)
    for proxy_var in (
        "no_proxy",
        "NO_PROXY",
        "http_proxy",
        "HTTP_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
    ):
        monkeypatch.delenv(proxy_var, raising=False)

    captured_calls: list[dict[str, Any]] = []
    orig_post = httpx.AsyncClient.post

    async def _mock_post_ok(
        self: httpx.AsyncClient,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if not str(url).startswith("https://"):
            return await orig_post(self, url, **kwargs)
        captured_calls.append({"url": url, "kwargs": kwargs})
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"recipient_id": "psid_1", "message_id": "mid_ok"}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post_ok)

    dm_res = await meta_graph_client.send_message(recipient_psid="psid_1", text="Hello")
    reply_res = await meta_graph_client.send_private_reply(comment_id="comment_1", text="Hi")
    assert dm_res["message_id"] == "mid_ok"
    assert reply_res["message_id"] == "mid_ok"
    assert len(captured_calls) == 2
    for call in captured_calls:
        assert call["kwargs"]["headers"]["Authorization"] == f"Bearer {secret_meta_token}"
        assert "access_token" not in (call["kwargs"].get("params") or {})
        assert secret_meta_token not in str(call["url"])

    # Simulate HTTP errors containing sensitive URLs/secrets in httpx exceptions
    async def _mock_post_http_error(
        self: httpx.AsyncClient,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if not str(url).startswith("https://"):
            return await orig_post(self, url, **kwargs)
        req = httpx.Request("POST", f"{url}?leaked={secret_meta_token}")
        return httpx.Response(
            502,
            json={"error": "upstream gateway error"},
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post_http_error)

    meta_payload = {
        "object": "page",
        "entry": [
            {
                "id": "page_123",
                "messaging": [
                    {
                        "sender": {"id": "psid_sec001"},
                        "recipient": {"id": "page_123"},
                        "message": {"mid": "mid_sec001", "text": "Cho mình hỏi giá Robusta"},
                    }
                ],
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "item": "comment",
                            "comment_id": "comment_sec001",
                            "message": "Tư vấn cà phê",
                        },
                    }
                ],
            }
        ],
    }
    raw_meta = json.dumps(meta_payload, separators=(",", ":")).encode("utf-8")

    with caplog.at_level(logging.WARNING):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            meta_resp = await client.post(
                "/api/v1/webhooks/meta",
                content=raw_meta,
                headers=meta_signature_headers(raw_meta, None),
            )
            assert meta_resp.status_code == 200
            assert secret_meta_token not in meta_resp.text
            for event in meta_resp.json()["events"]:
                assert event["meta_delivery"]["delivered"] is False
                assert event["meta_delivery"]["queued"] is True

            # Telegram dispatch error via direct client call and SePay webhook background task
            tg_direct = await telegram_dispatch_client.dispatch_order_alert(
                telegram_user_id="8499887766",
                order_code="DH1001",
                total_amount_vnd=640000,
                items_summary="2x COFFEE-ROBUSTA-1KG",
            )
            assert tg_direct["dispatched"] is False
            assert tg_direct["status_code"] == 502
            assert secret_tg_token not in json.dumps(tg_direct)

            sepay_resp = await client.post(
                "/api/v1/webhooks/sepay",
                json={
                    "id": 996601,
                    "gateway": "MBBank",
                    "transactionDate": "2026-11-20 10:00:00",
                    "accountNumber": "0123456789",
                    "content": "DH1001",
                    "transferType": "in",
                    "transferAmount": 640000,
                    "referenceCode": "FT_SEC001",
                },
                headers={"Authorization": f"Apikey {sepay_client.webhook_secret}"},
            )
            assert sepay_resp.status_code == 200
            assert secret_tg_token not in sepay_resp.text

    assert secret_meta_token not in caplog.text
    assert secret_tg_token not in caplog.text


@pytest.mark.asyncio
async def test_sec002_refresh_concurrency_unpersisted_jti_and_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    create_test_user: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
    """SEC-002: Concurrent /refresh allows single rotation (200+401), blocks unpersisted JTI, enforces 429."""
    user = await create_test_user(
        email="refresh-sec002@default-tenant.test",
        password="RefreshSec!123",
        role="admin",
        tenant_id="default-tenant",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Forged / unpersisted JTI refresh token -> 401
        unpersisted_refresh = create_refresh_token(
            user_id=user["id"],
            tenant_id="default-tenant",
            role="admin",
            email=str(user["email"]),
            jti=str(uuid4()),
        )
        unpersisted_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": unpersisted_refresh},
        )
        assert unpersisted_resp.status_code == 401

        # 2. Double / concurrent refresh with the same persisted refresh token -> [200, 401]
        login_resp = await client.post(
            "/api/v1/auth/token",
            json={
                "email": "refresh-sec002@default-tenant.test",
                "password": "RefreshSec!123",
                "tenant_id": "default-tenant",
            },
        )
        assert login_resp.status_code == 200
        refresh_token = login_resp.json()["refresh_token"]

        resp_a, resp_b = await asyncio.gather(
            client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token}),
            client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token}),
        )
        assert sorted([resp_a.status_code, resp_b.status_code]) == [200, 401]

        # Subsequent replay of the same refresh token -> 401
        replay_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert replay_resp.status_code == 401

        # 3. /refresh enforces 429 rate limit
        monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", "3")
        get_settings.cache_clear()
        from app.api.auth import auth_rate_limiter

        auth_rate_limiter.reset()

        for _ in range(3):
            r = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refresh_token},
            )
            assert r.status_code == 401

        rate_limited = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert rate_limited.status_code == 429
        assert int(rate_limited.headers["retry-after"]) >= 1


@pytest.mark.asyncio
async def test_obs002_invalid_or_cross_tenant_assigned_employee_returns_404_and_preserves_stock(
    auth_headers: dict[str, str],
) -> None:
    """OBS-002: Unknown or cross-tenant `assigned_employee_id` fails with 404 and restores stock."""
    customer = erp_repository.list_customers("default-tenant")[0]
    robusta = next(
        item
        for item in erp_repository.list_inventory("default-tenant")
        if item.sku == "COFFEE-ROBUSTA-1KG"
    )
    initial_stock = robusta.stock_quantity

    cross_tenant_emp_id = uuid4()
    erp_repository._employees[cross_tenant_emp_id] = EmployeeResponse(
        id=cross_tenant_emp_id,
        tenant_id="tenant-b",
        full_name="Tenant B Staff",
        role="fulfillment_specialist",
        telegram_user_id="9999999999",
        is_active=True,
        created_at=datetime.now(UTC),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for invalid_emp_id in (uuid4(), cross_tenant_emp_id):
            resp = await client.post(
                "/api/v1/erp/orders",
                json={
                    "customer_id": str(customer.id),
                    "assigned_employee_id": str(invalid_emp_id),
                    "items": [{"sku": "COFFEE-ROBUSTA-1KG", "quantity": 3}],
                },
                headers=auth_headers,
            )
            assert resp.status_code == 404

            current_stock = next(
                item
                for item in erp_repository.list_inventory("default-tenant")
                if item.sku == "COFFEE-ROBUSTA-1KG"
            ).stock_quantity
            assert current_stock == initial_stock

