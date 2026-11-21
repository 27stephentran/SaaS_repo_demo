"""Pytest configuration ensuring `backend/` is on `sys.path`, test isolation, and auth/webhook fixtures."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_API_TOKEN_SECRET = "test-api-token-secret-must-be-at-least-32-chars-0123456789"
TEST_META_APP_SECRET = "test-meta-app-secret-0123456789"
TEST_SEPAY_WEBHOOK_SECRET = "sepay-sandbox-secret"
TEST_META_VERIFY_TOKEN = "saas-meta-webhook-verify-token"
TEST_DEFAULT_TENANT_ID = "default-tenant"
TEST_DEMO_TENANT_ID = "demo-tenant"
TEST_CORS_ORIGINS = '["https://saa-s-repo-demo.vercel.app","http://localhost:3000"]'
TEST_SEPAY_ACCOUNT_NUMBER = "0123456789"
TEST_SEPAY_BANK_CODE = "MB"
TEST_SOFT_DELETE_RETENTION_DAYS = "365"

os.environ.setdefault("API_TOKEN_SECRET", TEST_API_TOKEN_SECRET)
os.environ.setdefault("META_APP_SECRET", TEST_META_APP_SECRET)
os.environ.setdefault("SEPAY_WEBHOOK_SECRET", TEST_SEPAY_WEBHOOK_SECRET)
os.environ.setdefault("META_VERIFY_TOKEN", TEST_META_VERIFY_TOKEN)
os.environ.setdefault("DEFAULT_TENANT_ID", TEST_DEFAULT_TENANT_ID)
os.environ.setdefault("DEMO_TENANT_ID", TEST_DEMO_TENANT_ID)
os.environ.setdefault("CORS_ORIGINS", TEST_CORS_ORIGINS)
os.environ.setdefault("SEPAY_ACCOUNT_NUMBER", TEST_SEPAY_ACCOUNT_NUMBER)
os.environ.setdefault("SEPAY_BANK_CODE", TEST_SEPAY_BANK_CODE)
os.environ.setdefault("SOFT_DELETE_RETENTION_DAYS", TEST_SOFT_DELETE_RETENTION_DAYS)

from app.api.auth import auth_rate_limiter  # noqa: E402
from app.api.demo import demo_rate_limiter  # noqa: E402
from app.core.auth import create_access_token, hash_password, hash_token  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import (  # noqa: E402
    AsyncSessionLocal,
    engine,
    erp_repository,
    tenant_db_session,
)
from app.services.integrations import meta_graph_client, sepay_client  # noqa: E402

get_settings.cache_clear()


def compute_meta_signature(raw_body: bytes, secret: str = TEST_META_APP_SECRET) -> str:
    """Compute Meta `X-Hub-Signature-256` HMAC-SHA256 signature header value."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def compute_sepay_hmac_headers(
    raw_body: bytes,
    secret: str = TEST_SEPAY_WEBHOOK_SECRET,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Compute SePay HMAC-SHA256 signature and timestamp headers over raw JSON bytes."""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    signed_payload = f"{ts}.".encode() + raw_body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-SePay-Timestamp": ts,
        "X-SePay-Signature": f"sha256={digest}",
    }


def _use_postgres_in_tests() -> bool:
    return os.getenv("USE_POSTGRES_IN_TESTS", "").strip().lower() in ("1", "true")


async def reset_postgres_test_state() -> None:
    """Truncate tenant tables and re-seed deterministic `default-tenant` fixtures in PostgreSQL."""
    candidate_tables = (
        "channel_messages",
        "channel_conversations",
        "outbox_jobs",
        "webhook_dedup_events",
        "api_tokens",
        "users",
        "sepay_webhook_logs",
        "orders",
        "employees",
        "customers",
        "inventory",
        "tenant_channels",
        "companies",
    )
    session_factory = AsyncSessionLocal
    if session_factory is None:
        raise RuntimeError("AsyncSessionLocal is not initialized")
    async with session_factory() as session:
        async with session.begin():
            existing_res = await session.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = current_schema()"
                )
            )
            existing_tables = {str(row[0]) for row in existing_res.fetchall()}
            tables_to_truncate = [t for t in candidate_tables if t in existing_tables]
            if tables_to_truncate:
                await session.execute(
                    text(
                        f"TRUNCATE TABLE {', '.join(tables_to_truncate)} "
                        "RESTART IDENTITY CASCADE"
                    )
                )
            await session.execute(text("ALTER SEQUENCE order_code_seq RESTART WITH 1002"))

    customer_id = UUID("11111111-1111-1111-1111-111111111111")
    employee_id = UUID("22222222-2222-2222-2222-222222222222")
    order_id = UUID("44444444-4444-4444-4444-444444444401")
    qr_url = "https://qr.sepay.vn/img?acc=0123456789&bank=MB&amount=640000&des=DH1001"
    order_items = [
        {
            "sku": "COFFEE-ROBUSTA-1KG",
            "name": "Fine Robusta Dak Lak Whole Bean 1kg",
            "quantity": 2,
            "unit_price_vnd": 320000,
        }
    ]

    async with tenant_db_session("default-tenant") as session:
        await session.execute(
            text(
                """
                INSERT INTO customers (id, tenant_id, meta_psid, full_name, phone)
                VALUES (:id, 'default-tenant', 'psid_vn_customer_001', 'Nguyen Minh Anh', '+84901234567')
                """
            ),
            {"id": customer_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO employees (id, tenant_id, full_name, role, telegram_user_id, is_active)
                VALUES (:id, 'default-tenant', 'Tran Hoang Nam (Warehouse Lead)', 'fulfillment_specialist', '8499887766', TRUE)
                """
            ),
            {"id": employee_id},
        )
        for item_id, sku, name, desc, category, price, stock in (
            (
                UUID("33333333-3333-3333-3333-333333333301"),
                "COFFEE-ROBUSTA-1KG",
                "Fine Robusta Dak Lak Whole Bean 1kg",
                "High-altitude honey-processed Fine Robusta coffee beans from Buon Ma Thuot, notes of dark chocolate and caramel.",
                "Hardware",
                320000,
                45,
            ),
            (
                UUID("33333333-3333-3333-3333-333333333302"),
                "COFFEE-ARABICA-500G",
                "Cau Dat Specialty Arabica 500g",
                "Washed specialty Arabica from Da Lat 1600m elevation, floral aroma, citrus acidity, ideal for pour-over V60.",
                "Software",
                285000,
                28,
            ),
            (
                UUID("33333333-3333-3333-3333-333333333303"),
                "EQUIP-V60-KIT",
                "Ceramic V60 Pour-Over Brewer Kit",
                "Complete specialty coffee brewing kit including ceramic dripper, 600ml glass server, and 100 bleached paper filters.",
                "Hardware",
                650000,
                14,
            ),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO inventory (id, tenant_id, sku, name, description, category, price_vnd, stock_quantity)
                    VALUES (:id, 'default-tenant', :sku, :name, :description, :category, :price_vnd, :stock_quantity)
                    """
                ),
                {
                    "id": item_id,
                    "sku": sku,
                    "name": name,
                    "description": desc,
                    "category": category,
                    "price_vnd": price,
                    "stock_quantity": stock,
                },
            )
        await session.execute(
            text(
                """
                INSERT INTO orders (
                    id, tenant_id, order_code, customer_id, assigned_employee_id,
                    status, total_amount_vnd, sepay_qr_url, items_json
                )
                VALUES (
                    :id, 'default-tenant', 'DH1001', :customer_id, :employee_id,
                    'PENDING_PAYMENT', 640000, :qr_url, CAST(:items_json AS jsonb)
                )
                """
            ),
            {
                "id": order_id,
                "customer_id": customer_id,
                "employee_id": employee_id,
                "qr_url": qr_url,
                "items_json": json.dumps(order_items),
            },
        )


@pytest.fixture(autouse=True)
def isolate_test_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset in-memory repository/rate-limit/idempotency state and deterministic test secrets per test."""
    monkeypatch.setenv("API_TOKEN_SECRET", TEST_API_TOKEN_SECRET)
    monkeypatch.setenv("META_APP_SECRET", TEST_META_APP_SECRET)
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", TEST_SEPAY_WEBHOOK_SECRET)
    monkeypatch.setenv("META_VERIFY_TOKEN", TEST_META_VERIFY_TOKEN)
    monkeypatch.setenv("DEFAULT_TENANT_ID", TEST_DEFAULT_TENANT_ID)
    monkeypatch.setenv("DEMO_TENANT_ID", TEST_DEMO_TENANT_ID)
    monkeypatch.setenv("CORS_ORIGINS", TEST_CORS_ORIGINS)
    monkeypatch.setenv("SEPAY_ACCOUNT_NUMBER", TEST_SEPAY_ACCOUNT_NUMBER)
    monkeypatch.setenv("SEPAY_BANK_CODE", TEST_SEPAY_BANK_CODE)
    monkeypatch.setenv("SOFT_DELETE_RETENTION_DAYS", TEST_SOFT_DELETE_RETENTION_DAYS)
    get_settings.cache_clear()

    erp_repository.reset()
    auth_rate_limiter.reset()
    demo_rate_limiter.reset()
    sepay_client._processed_transactions.clear()

    monkeypatch.setattr(meta_graph_client, "app_secret", TEST_META_APP_SECRET)
    monkeypatch.setattr(sepay_client, "webhook_secret", TEST_SEPAY_WEBHOOK_SECRET)
    monkeypatch.setattr(sepay_client, "account_number", TEST_SEPAY_ACCOUNT_NUMBER)
    monkeypatch.setattr(sepay_client, "bank_code", TEST_SEPAY_BANK_CODE)

    yield

    erp_repository.reset()
    auth_rate_limiter.reset()
    demo_rate_limiter.reset()
    sepay_client._processed_transactions.clear()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def isolate_postgres_test_state() -> AsyncIterator[None]:
    """When `USE_POSTGRES_IN_TESTS=1`, reset and re-seed PostgreSQL tables around each test."""
    try:
        if _use_postgres_in_tests():
            if engine is not None:
                await engine.dispose()
            await reset_postgres_test_state()
        yield
    finally:
        if _use_postgres_in_tests():
            try:
                await reset_postgres_test_state()
            finally:
                if engine is not None:
                    await engine.dispose()


@pytest.fixture
def create_test_user() -> Callable[..., Awaitable[dict[str, Any]]]:
    """Factory fixture to create tenant-scoped test user accounts with scrypt password hashes."""

    async def _create(
        email: str = "staff@default-tenant.test",
        password: str = "StrongTestPass!123",
        role: str = "admin",
        tenant_id: str = "default-tenant",
        is_active: bool = True,
    ) -> dict[str, Any]:
        return await erp_repository.create_user(
            email=email,
            password_hash=hash_password(password),
            role=role,
            tenant_id=tenant_id,
            is_active=is_active,
        )

    return _create


async def _issue_bearer_headers(
    *,
    email: str,
    role: str,
    tenant_id: str,
) -> dict[str, str]:
    user = await erp_repository.create_user(
        email=email,
        password_hash=hash_password("QaPassword!123"),
        role=role,
        tenant_id=tenant_id,
    )
    jti = str(uuid4())
    token = create_access_token(
        user_id=user["id"],
        tenant_id=tenant_id,
        role=role,
        email=str(user["email"]),
        jti=jti,
    )
    await erp_repository.store_token_record(
        user_id=user["id"],
        jti=jti,
        token_hash=hash_token(token),
        token_type="access",
        expires_at=datetime.now(UTC) + timedelta(seconds=900),
        tenant_id=tenant_id,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def auth_headers() -> dict[str, str]:
    """Valid Bearer access token headers for `default-tenant`."""
    return await _issue_bearer_headers(
        email="qa-admin@default-tenant.test",
        role="admin",
        tenant_id="default-tenant",
    )


@pytest.fixture
async def tenant_b_headers() -> dict[str, str]:
    """Valid Bearer access token headers for isolated `tenant-b`."""
    return await _issue_bearer_headers(
        email="qa-admin@tenant-b.test",
        role="admin",
        tenant_id="tenant-b",
    )


@pytest.fixture
def meta_signature_headers() -> Callable[[bytes, str | None], dict[str, str]]:
    """Helper fixture returning Content-Type and valid `X-Hub-Signature-256` headers."""

    def _headers(raw_body: bytes, secret: str | None = None) -> dict[str, str]:
        effective_secret = secret if secret is not None else meta_graph_client.app_secret
        return {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": compute_meta_signature(raw_body, effective_secret),
        }

    return _headers


@pytest.fixture
def sepay_hmac_headers() -> Callable[[bytes, str | None, int | None], dict[str, str]]:
    """Helper fixture returning valid SePay HMAC-SHA256 headers."""

    def _headers(
        raw_body: bytes,
        secret: str | None = None,
        timestamp: int | None = None,
    ) -> dict[str, str]:
        effective_secret = secret if secret is not None else sepay_client.webhook_secret
        return compute_sepay_hmac_headers(
            raw_body,
            secret=effective_secret,
            timestamp=timestamp,
        )

    return _headers
