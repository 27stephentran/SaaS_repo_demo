"""Async SQLAlchemy + asyncpg database layer, RLS session context, and pgvector `<=>` cosine search."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.entities import (
    CustomerResponse,
    EmployeeResponse,
    InventoryCreateRequest,
    InventoryResponse,
    OrderCreateRequest,
    OrderLineItem,
    OrderResponse,
    OrderStatus,
    WebhookLogResponse,
)

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/saas_erp_mvp"

settings = get_settings()

_use_null_pool = (
    os.getenv("USE_POSTGRES_IN_TESTS", "").strip().lower() in ("1", "true")
    or bool(os.getenv("PYTEST_CURRENT_TEST"))
    or "pytest" in sys.modules
)

# SQLAlchemy 2.0 AsyncEngine configured for Supabase PostgreSQL + Supavisor pooling
engine: AsyncEngine = (
    create_async_engine(
        settings.database_url,
        echo=False,
        poolclass=NullPool,
        future=True,
        connect_args={"statement_cache_size": 0},
    )
    if _use_null_pool
    else create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        future=True,
        connect_args={"statement_cache_size": 0},
    )
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Canonical SQL query exercising Supabase `pgvector` `<=>` cosine distance operator
PGVECTOR_SIMILARITY_SQL = text(
    """
    SELECT
        id,
        tenant_id,
        sku,
        name,
        description,
        category,
        price_vnd,
        stock_quantity,
        (embedding <=> CAST(:query_embedding AS vector(768))) AS distance,
        1.0 - (embedding <=> CAST(:query_embedding AS vector(768))) AS similarity_score,
        created_at,
        updated_at
    FROM inventory
    WHERE tenant_id = :tenant_id
      AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:query_embedding AS vector(768)) ASC
    LIMIT :limit
    """
)


def _is_production_env() -> bool:
    """Return True when running in production mode where DB fallback is forbidden."""
    env_val = os.getenv("APP_ENV", getattr(settings, "app_env", "development"))
    return str(env_val).strip().lower() == "production"


def _normalize_tenant_id(tenant_id: str) -> str:
    """Normalize default tenant aliases while failing closed on empty tenant IDs."""
    if not isinstance(tenant_id, str):
        raise ValueError("tenant_id must be a non-empty string")
    cleaned = tenant_id.strip()
    if not cleaned:
        raise ValueError("tenant_id must not be empty")
    if cleaned in ("default-tenant", "tenant-default"):
        return "default-tenant"
    return cleaned


@asynccontextmanager
async def tenant_db_session(tenant_id: str) -> AsyncGenerator[AsyncSession, None]:
    """Open an explicit transaction and bind `app.tenant_id` for PostgreSQL RLS."""
    norm_tenant = _normalize_tenant_id(tenant_id)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": norm_tenant},
            )
            yield session


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async SQLAlchemy session."""
    async with AsyncSessionLocal() as session:
        yield session


def compute_cosine_distance(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine distance (`1 - cosine_similarity`) between two 768-dim vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 1.0
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    similarity = max(-1.0, min(1.0, dot_product / (norm_a * norm_b)))
    return round(1.0 - similarity, 6)


def _extract_sku_qty(item: Any) -> tuple[str, int]:
    """Extract `(sku, quantity)` from either a dict or a Pydantic line-item object."""
    if isinstance(item, dict):
        return str(item["sku"]), int(item["quantity"])
    return str(item.sku), int(item.quantity)


_OUTBOX_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bbot\d+:[A-Za-z0-9_-]+\b"), "bot[REDACTED]"),
    (re.compile(r"\b(?:Bearer|Apikey)\s+\S+", re.IGNORECASE), "[REDACTED_AUTH]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{16,})\b"), "[REDACTED_KEY]"),
    (
        re.compile(
            r"([?&](?:access_token|token|api_key|secret|password)=)[^&\s]+",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
)


def _sanitize_outbox_error(error_message: str, max_len: int = 500) -> str:
    """Redact potential credentials and truncate outbox failure strings safely."""
    cleaned = (error_message or "").strip()
    for pattern, replacement in _OUTBOX_SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3] + "..."
    return cleaned


class ERPRepository:
    """Repository providing ERP state persistence, RLS-aware async PostgreSQL ops, and `pgvector` search."""

    def __init__(self) -> None:
        self._inventory: dict[UUID, dict[str, Any]] = {}
        self._customers: dict[UUID, CustomerResponse] = {}
        self._employees: dict[UUID, EmployeeResponse] = {}
        self._orders: dict[UUID, OrderResponse] = {}
        self._sepay_webhook_logs: dict[str, dict[str, Any]] = {}
        self._users: dict[UUID, dict[str, Any]] = {}
        self._api_tokens: dict[str, dict[str, Any]] = {}
        self._tenant_channels: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._webhook_dedup_events: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._outbox_jobs: dict[UUID, dict[str, Any]] = {}
        self._order_code_counter: int = 1001
        self._seed_defaults(datetime.now(UTC))

    def reset(self) -> None:
        """Reset repository state and re-seed deterministic fixture defaults for test isolation."""
        self._inventory.clear()
        self._customers.clear()
        self._employees.clear()
        self._orders.clear()
        self._sepay_webhook_logs.clear()
        self._users.clear()
        self._api_tokens.clear()
        self._tenant_channels.clear()
        self._webhook_dedup_events.clear()
        self._outbox_jobs.clear()
        self._order_code_counter = 1001
        self._seed_defaults(datetime.now(UTC))

    def _seed_defaults(self, now: datetime) -> None:
        customer_id = UUID("11111111-1111-1111-1111-111111111111")
        employee_id = UUID("22222222-2222-2222-2222-222222222222")

        self._customers[customer_id] = CustomerResponse(
            id=customer_id,
            tenant_id="default-tenant",
            meta_psid="psid_vn_customer_001",
            full_name="Nguyen Minh Anh",
            phone="+84901234567",
            created_at=now,
        )

        self._employees[employee_id] = EmployeeResponse(
            id=employee_id,
            tenant_id="default-tenant",
            full_name="Tran Hoang Nam (Warehouse Lead)",
            role="fulfillment_specialist",
            telegram_user_id="8499887766",
            is_active=True,
            created_at=now,
        )

        default_items = [
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
        ]

        for item_id, sku, name, desc, category, price, stock in default_items:
            self._inventory[item_id] = {
                "id": item_id,
                "tenant_id": "default-tenant",
                "sku": sku,
                "name": name,
                "description": desc,
                "category": category,
                "price_vnd": price,
                "stock_quantity": stock,
                "embedding": None,
                "created_at": now,
                "updated_at": now,
            }

        initial_order_id = UUID("44444444-4444-4444-4444-444444444401")
        qr_url = "https://qr.sepay.vn/img?acc=0123456789&bank=MB&amount=640000&des=DH1001"
        self._orders[initial_order_id] = OrderResponse(
            id=initial_order_id,
            tenant_id="default-tenant",
            order_code="DH1001",
            customer_id=customer_id,
            customer_name="Nguyen Minh Anh",
            assigned_employee_id=employee_id,
            status=OrderStatus.PENDING_PAYMENT,
            total_amount_vnd=640000,
            sepay_qr_url=qr_url,
            vietqr_url=qr_url,
            sepay_transaction_id=None,
            sepay_reference="DH1001",
            items_json=[
                {
                    "sku": "COFFEE-ROBUSTA-1KG",
                    "name": "Fine Robusta Dak Lak Whole Bean 1kg",
                    "quantity": 2,
                    "unit_price_vnd": 320000,
                }
            ],
            created_at=now,
            updated_at=now,
        )

    async def is_postgres_active(self) -> bool:
        """Return True when PostgreSQL is configured and reachable; fail closed in production."""
        current_url = os.getenv("DATABASE_URL") or settings.database_url
        is_prod = _is_production_env()
        force_pg_tests = os.getenv("USE_POSTGRES_IN_TESTS", "").strip().lower() in ("1", "true")

        if not current_url or (current_url == DEFAULT_LOCAL_DATABASE_URL and not force_pg_tests):
            if is_prod:
                raise RuntimeError(
                    "PostgreSQL DATABASE_URL must be configured in production mode (fail-closed)"
                )
            return False

        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            if is_prod:
                logger.error("PostgreSQL unavailable in production mode: %s", exc)
                raise RuntimeError("PostgreSQL database unavailable in production mode") from exc
            logger.warning("PostgreSQL unreachable in non-production mode; using local fallback: %s", exc)
            return False

    # --------------------------------------------------------------------------
    # Synchronous Repository Methods (preserved for T1.1 backward compatibility)
    # --------------------------------------------------------------------------

    def list_inventory(self, tenant_id: str = "default-tenant") -> list[InventoryResponse]:
        norm_tenant = _normalize_tenant_id(tenant_id)
        results: list[InventoryResponse] = []
        for raw in self._inventory.values():
            if _normalize_tenant_id(raw["tenant_id"]) == norm_tenant:
                results.append(
                    InventoryResponse(
                        id=raw["id"],
                        tenant_id=raw["tenant_id"],
                        sku=raw["sku"],
                        name=raw["name"],
                        description=raw["description"],
                        category=raw.get("category", "Hardware"),
                        price_vnd=raw["price_vnd"],
                        stock_quantity=raw["stock_quantity"],
                        has_embedding=raw["embedding"] is not None,
                        similarity_distance=None,
                        similarity_score=None,
                        created_at=raw["created_at"],
                        updated_at=raw["updated_at"],
                    )
                )
        return results

    def create_inventory_item(
        self, payload: InventoryCreateRequest, embedding: list[float]
    ) -> InventoryResponse:
        now = datetime.now(UTC)
        item_id = uuid4()
        norm_tenant = _normalize_tenant_id(payload.tenant_id)
        record = {
            "id": item_id,
            "tenant_id": norm_tenant,
            "sku": payload.sku,
            "name": payload.name,
            "description": payload.description,
            "category": payload.category,
            "price_vnd": payload.price_vnd,
            "stock_quantity": payload.stock_quantity,
            "embedding": embedding,
            "created_at": now,
            "updated_at": now,
        }
        self._inventory[item_id] = record
        return InventoryResponse(
            id=item_id,
            tenant_id=norm_tenant,
            sku=payload.sku,
            name=payload.name,
            description=payload.description,
            category=payload.category,
            price_vnd=payload.price_vnd,
            stock_quantity=payload.stock_quantity,
            has_embedding=bool(embedding),
            similarity_distance=None,
            similarity_score=None,
            created_at=now,
            updated_at=now,
        )

    def search_inventory_by_embedding(
        self,
        query_embedding: list[float],
        query_text: str,
        tenant_id: str = "default-tenant",
        limit: int = 5,
        embedding_fn: Any | None = None,
    ) -> list[InventoryResponse]:
        norm_tenant = _normalize_tenant_id(tenant_id)
        scored: list[tuple[float, dict[str, Any]]] = []
        query_terms = set(query_text.lower().split())

        for raw in self._inventory.values():
            if _normalize_tenant_id(raw["tenant_id"]) != norm_tenant:
                continue
            item_embedding = raw.get("embedding")
            if item_embedding is None and embedding_fn is not None:
                item_embedding = embedding_fn(f"{raw['name']} {raw['description']}")
                raw["embedding"] = item_embedding

            distance = (
                compute_cosine_distance(query_embedding, item_embedding)
                if item_embedding
                else 1.0
            )
            # Combine vector cosine distance with lexical boost for deterministic ranking
            haystack = f"{raw['sku']} {raw['name']} {raw['description']} {raw.get('category', '')}".lower()
            lexical_hits = sum(1 for term in query_terms if term in haystack)
            adjusted_distance = max(0.0, round(distance - (0.08 * lexical_hits), 6))
            scored.append((adjusted_distance, raw))

        scored.sort(key=lambda pair: pair[0])
        results: list[InventoryResponse] = []
        for dist, raw in scored[:limit]:
            sim_dist = round(dist, 6)
            sim_score = round(max(0.0, 1.0 - dist / 2.0), 4)
            results.append(
                InventoryResponse(
                    id=raw["id"],
                    tenant_id=raw["tenant_id"],
                    sku=raw["sku"],
                    name=raw["name"],
                    description=raw["description"],
                    category=raw.get("category", "Hardware"),
                    price_vnd=raw["price_vnd"],
                    stock_quantity=raw["stock_quantity"],
                    has_embedding=raw["embedding"] is not None,
                    similarity_distance=sim_dist,
                    similarity_score=sim_score,
                    created_at=raw["created_at"],
                    updated_at=raw["updated_at"],
                )
            )
        return results

    search_inventory_cosine = search_inventory_by_embedding

    def list_customers(self, tenant_id: str = "default-tenant") -> list[CustomerResponse]:
        norm_tenant = _normalize_tenant_id(tenant_id)
        return [
            c for c in self._customers.values() if _normalize_tenant_id(c.tenant_id) == norm_tenant
        ]

    def get_or_create_customer_by_psid(
        self, meta_psid: str, full_name: str = "Meta Shopper", tenant_id: str = "default-tenant"
    ) -> CustomerResponse:
        norm_tenant = _normalize_tenant_id(tenant_id)
        for customer in self._customers.values():
            if customer.meta_psid == meta_psid and _normalize_tenant_id(customer.tenant_id) == norm_tenant:
                return customer
        new_id = uuid4()
        customer = CustomerResponse(
            id=new_id,
            tenant_id=norm_tenant,
            meta_psid=meta_psid,
            full_name=full_name,
            phone=None,
            created_at=datetime.now(UTC),
        )
        self._customers[new_id] = customer
        return customer

    def list_employees(self, tenant_id: str = "default-tenant") -> list[EmployeeResponse]:
        norm_tenant = _normalize_tenant_id(tenant_id)
        return [
            e for e in self._employees.values() if _normalize_tenant_id(e.tenant_id) == norm_tenant
        ]

    def get_employee(
        self, employee_id: UUID | None, tenant_id: str = "default-tenant"
    ) -> EmployeeResponse | None:
        norm_tenant = _normalize_tenant_id(tenant_id)
        if employee_id and employee_id in self._employees:
            emp = self._employees[employee_id]
            if _normalize_tenant_id(emp.tenant_id) == norm_tenant:
                return emp
        # Fallback to first active employee in the tenant
        for emp in self._employees.values():
            if emp.is_active and _normalize_tenant_id(emp.tenant_id) == norm_tenant:
                return emp
        return None

    def list_orders(self, tenant_id: str = "default-tenant") -> list[OrderResponse]:
        norm_tenant = _normalize_tenant_id(tenant_id)
        return [
            o for o in self._orders.values() if _normalize_tenant_id(o.tenant_id) == norm_tenant
        ]

    def create_order(
        self, payload: OrderCreateRequest, sepay_qr_url: str, order_code: str
    ) -> OrderResponse:
        now = datetime.now(UTC)
        order_id = uuid4()
        norm_tenant = _normalize_tenant_id(payload.tenant_id)
        resolved_items: list[OrderLineItem] = []
        for item in payload.items:
            inv_record = next(
                (
                    raw
                    for raw in self._inventory.values()
                    if _normalize_tenant_id(str(raw["tenant_id"])) == norm_tenant
                    and raw["sku"] == item.sku
                ),
                None,
            )
            resolved_name = (
                item.name
                if item.name is not None
                else (str(inv_record["name"]) if inv_record is not None else item.sku)
            )
            resolved_price = (
                item.unit_price_vnd
                if item.unit_price_vnd is not None
                else (int(inv_record["price_vnd"]) if inv_record is not None else 0)
            )
            resolved_items.append(
                OrderLineItem(
                    sku=item.sku,
                    name=resolved_name,
                    quantity=item.quantity,
                    unit_price_vnd=resolved_price,
                )
            )
        total_amount = sum(item.quantity * item.unit_price_vnd for item in resolved_items)
        assigned_emp_id = payload.assigned_employee_id
        if assigned_emp_id is None:
            default_emp = self.get_employee(None, tenant_id=norm_tenant)
            assigned_emp_id = default_emp.id if default_emp else None

        customer = self._customers.get(payload.customer_id)
        customer_name = customer.full_name if customer else "Enterprise Customer"
        items_dump = [item.model_dump() for item in resolved_items]

        order = OrderResponse(
            id=order_id,
            tenant_id=norm_tenant,
            order_code=order_code,
            customer_id=payload.customer_id,
            customer_name=customer_name,
            assigned_employee_id=assigned_emp_id,
            status=OrderStatus.PENDING_PAYMENT,
            total_amount_vnd=total_amount,
            sepay_qr_url=sepay_qr_url,
            vietqr_url=sepay_qr_url,
            sepay_transaction_id=None,
            sepay_reference=order_code,
            items_json=items_dump,
            items=resolved_items,
            created_at=now,
            updated_at=now,
        )
        self._orders[order_id] = order
        return order

    def update_order_status(
        self, order_id: UUID, new_status: OrderStatus, tenant_id: str = "default-tenant"
    ) -> OrderResponse | None:
        norm_tenant = _normalize_tenant_id(tenant_id)
        existing = self._orders.get(order_id)
        if not existing or _normalize_tenant_id(existing.tenant_id) != norm_tenant:
            return None
        updated = existing.model_copy(
            update={"status": new_status, "updated_at": datetime.now(UTC)}
        )
        self._orders[order_id] = updated
        return updated

    def get_order_by_code(
        self, order_code: str, tenant_id: str = "default-tenant"
    ) -> OrderResponse | None:
        norm_tenant = _normalize_tenant_id(tenant_id)
        normalized_code = order_code.upper().strip()
        for existing in self._orders.values():
            if (
                existing.order_code.upper() == normalized_code
                and _normalize_tenant_id(existing.tenant_id) == norm_tenant
            ):
                return existing
        return None

    def mark_order_paid_by_code(
        self, order_code: str, transaction_id: str, tenant_id: str = "default-tenant"
    ) -> OrderResponse | None:
        norm_tenant = _normalize_tenant_id(tenant_id)
        normalized_code = order_code.upper().strip()
        for order_id, existing in self._orders.items():
            if (
                existing.order_code.upper() == normalized_code
                and _normalize_tenant_id(existing.tenant_id) == norm_tenant
            ):
                if existing.status != OrderStatus.PENDING_PAYMENT:
                    return existing
                updated = existing.model_copy(
                    update={
                        "status": OrderStatus.PAID,
                        "sepay_transaction_id": transaction_id,
                        "sepay_reference": transaction_id,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self._orders[order_id] = updated
                return updated
        return None

    def is_sepay_transaction_recorded(self, transaction_id: str) -> bool:
        return transaction_id in self._sepay_webhook_logs

    def get_sepay_webhook_log(self, transaction_id: str) -> dict[str, Any] | None:
        return self._sepay_webhook_logs.get(transaction_id)

    def record_sepay_webhook_log(
        self,
        transaction_id: str,
        order_code: str | None,
        transfer_type: str,
        transfer_amount: int,
        processing_status: str,
        raw_payload: dict[str, Any],
        tenant_id: str = "default-tenant",
        account_number: str | None = None,
        sub_account: str | None = None,
        reference_code: str | None = None,
    ) -> bool:
        """Atomically record a SePay transaction log in memory; returns False if already recorded."""
        if transaction_id in self._sepay_webhook_logs:
            return False
        self._sepay_webhook_logs[transaction_id] = {
            "id": len(self._sepay_webhook_logs) + 1,
            "sepay_transaction_id": transaction_id,
            "tenant_id": _normalize_tenant_id(tenant_id),
            "order_code": order_code,
            "transfer_type": transfer_type,
            "transfer_amount": transfer_amount,
            "account_number": account_number,
            "sub_account": sub_account,
            "reference_code": reference_code,
            "processing_status": processing_status,
            "raw_payload": raw_payload,
            "created_at": datetime.now(UTC),
        }
        return True

    # --------------------------------------------------------------------------
    # Async PostgreSQL-Backed Methods (with RLS & Fail-Closed Production Guard)
    # --------------------------------------------------------------------------

    async def persist_sepay_webhook_to_db(
        self,
        transaction_id: str,
        order_code: str | None,
        transfer_type: str,
        transfer_amount: int,
        account_number: str | None,
        sub_account: str | None,
        reference_code: str | None,
        processing_status: str,
        raw_payload: dict[str, Any],
        mark_paid: bool = False,
        tenant_id: str = "default-tenant",
    ) -> bool:
        """Persist SePay webhook deduplication log and PAID transition to PostgreSQL when active.

        Returns False only if PostgreSQL already holds this `sepay_transaction_id` (duplicate).
        """
        norm_tenant = _normalize_tenant_id(tenant_id)
        if not await self.is_postgres_active():
            return True

        try:
            async with tenant_db_session(norm_tenant) as session:
                insert_stmt = text(
                    """
                    INSERT INTO sepay_webhook_logs (
                        sepay_transaction_id,
                        tenant_id,
                        order_code,
                        transfer_type,
                        transfer_amount,
                        account_number,
                        sub_account,
                        reference_code,
                        processing_status,
                        raw_payload
                    )
                    VALUES (
                        :tx_id,
                        :tenant_id,
                        :order_code,
                        :transfer_type,
                        :transfer_amount,
                        :account_number,
                        :sub_account,
                        :reference_code,
                        :processing_status,
                        CAST(:raw_payload AS jsonb)
                    )
                    ON CONFLICT (sepay_transaction_id) DO NOTHING
                    RETURNING id
                    """
                )
                result = await session.execute(
                    insert_stmt,
                    {
                        "tx_id": transaction_id,
                        "tenant_id": norm_tenant,
                        "order_code": order_code,
                        "transfer_type": transfer_type,
                        "transfer_amount": transfer_amount,
                        "account_number": account_number,
                        "sub_account": sub_account,
                        "reference_code": reference_code,
                        "processing_status": processing_status,
                        "raw_payload": json.dumps(raw_payload),
                    },
                )
                inserted_id = result.scalar_one_or_none()
                if inserted_id is None:
                    return False

                if mark_paid and order_code:
                    update_stmt = text(
                        """
                        UPDATE orders
                        SET status = 'PAID',
                            sepay_transaction_id = :tx_id,
                            updated_at = NOW()
                        WHERE UPPER(order_code) = :order_code
                          AND tenant_id = :tenant_id
                          AND status = 'PENDING_PAYMENT'
                        """
                    )
                    await session.execute(
                        update_stmt,
                        {
                            "tx_id": transaction_id,
                            "order_code": order_code.upper().strip(),
                            "tenant_id": norm_tenant,
                        },
                    )
                return True
        except Exception as exc:  # pragma: no cover - propagate DB errors so SePay retries
            logger.error("PostgreSQL SePay persistence failed: %s", exc)
            raise

    async def resolve_channel_tenant(
        self,
        channel_type: str,
        external_id: str,
        sub_account: str = "",
    ) -> str | None:
        """Resolve owning `tenant_id` for an inbound webhook channel before RLS context is set."""
        norm_sub = (sub_account or "").strip()
        if await self.is_postgres_active():
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    stmt = text(
                        "SELECT resolve_tenant_by_channel(:channel_type, :external_id, :sub_account)"
                    )
                    result = await session.execute(
                        stmt,
                        {
                            "channel_type": channel_type,
                            "external_id": external_id.strip(),
                            "sub_account": norm_sub,
                        },
                    )
                    return result.scalar_one_or_none()

        record = self._tenant_channels.get((channel_type, external_id.strip(), norm_sub))
        if record and record.get("is_active", True):
            return str(record["tenant_id"])
        return None

    async def next_order_code(
        self, tenant_id: str = "default-tenant", prefix: str = "DH"
    ) -> str:
        """Generate a globally monotonic order code backed by `order_code_seq`."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                result = await session.execute(text("SELECT nextval('order_code_seq')"))
                seq_val = int(result.scalar_one())
                return f"{prefix}{seq_val}"

        self._order_code_counter += 1
        return f"{prefix}{self._order_code_counter}"

    async def atomic_decrement_stock(
        self,
        items: Sequence[Any],
        tenant_id: str = "default-tenant",
    ) -> bool:
        """Atomically decrement inventory stock for all order line items; roll back if any is short."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        parsed = sorted((_extract_sku_qty(item) for item in items), key=lambda pair: pair[0])
        if not parsed:
            return True

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                update_stmt = text(
                    """
                    UPDATE inventory
                    SET stock_quantity = stock_quantity - :qty,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND sku = :sku
                      AND stock_quantity >= :qty
                    RETURNING id
                    """
                )
                for sku, qty in parsed:
                    if qty <= 0:
                        await session.rollback()
                        return False
                    result = await session.execute(
                        update_stmt,
                        {"tenant_id": norm_tenant, "sku": sku, "qty": qty},
                    )
                    if result.scalar_one_or_none() is None:
                        await session.rollback()
                        return False
                return True

        # Local/test atomic pre-check before mutation
        matched_records: list[tuple[dict[str, Any], int]] = []
        for sku, qty in parsed:
            if qty <= 0:
                return False
            found = next(
                (
                    raw
                    for raw in self._inventory.values()
                    if _normalize_tenant_id(raw["tenant_id"]) == norm_tenant and raw["sku"] == sku
                ),
                None,
            )
            if found is None or int(found["stock_quantity"]) < qty:
                return False
            matched_records.append((found, qty))

        now = datetime.now(UTC)
        for found, qty in matched_records:
            found["stock_quantity"] = int(found["stock_quantity"]) - qty
            found["updated_at"] = now
        return True

    async def restore_stock(
        self,
        items: Sequence[Any],
        tenant_id: str = "default-tenant",
    ) -> None:
        """Restore previously decremented stock quantities (e.g., on order cancellation)."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        parsed = sorted((_extract_sku_qty(item) for item in items), key=lambda pair: pair[0])
        if not parsed:
            return

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                update_stmt = text(
                    """
                    UPDATE inventory
                    SET stock_quantity = stock_quantity + :qty,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND sku = :sku
                    """
                )
                for sku, qty in parsed:
                    if qty > 0:
                        await session.execute(
                            update_stmt,
                            {"tenant_id": norm_tenant, "sku": sku, "qty": qty},
                        )
            return

        now = datetime.now(UTC)
        for sku, qty in parsed:
            if qty <= 0:
                continue
            for raw in self._inventory.values():
                if _normalize_tenant_id(raw["tenant_id"]) == norm_tenant and raw["sku"] == sku:
                    raw["stock_quantity"] = int(raw["stock_quantity"]) + qty
                    raw["updated_at"] = now

    async def record_webhook_dedup(
        self,
        provider: str,
        external_event_id: str,
        payload_hash: str,
        tenant_id: str = "default-tenant",
    ) -> bool:
        """Record an inbound webhook event idempotently; returns False on duplicate delivery."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    INSERT INTO webhook_dedup_events (
                        tenant_id,
                        provider,
                        external_event_id,
                        payload_hash
                    )
                    VALUES (:tenant_id, :provider, :external_event_id, :payload_hash)
                    ON CONFLICT (tenant_id, provider, external_event_id) DO NOTHING
                    RETURNING id
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "provider": provider,
                        "external_event_id": external_event_id,
                        "payload_hash": payload_hash,
                    },
                )
                return result.scalar_one_or_none() is not None

        key = (norm_tenant, provider, external_event_id)
        if key in self._webhook_dedup_events:
            return False
        self._webhook_dedup_events[key] = {
            "id": len(self._webhook_dedup_events) + 1,
            "tenant_id": norm_tenant,
            "provider": provider,
            "external_event_id": external_event_id,
            "payload_hash": payload_hash,
            "created_at": datetime.now(UTC),
        }
        return True

    async def enqueue_outbox_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        tenant_id: str = "default-tenant",
    ) -> UUID:
        """Enqueue a transactional outbox job for reliable async downstream dispatch."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    INSERT INTO outbox_jobs (
                        tenant_id,
                        job_type,
                        payload,
                        status,
                        attempts,
                        next_retry_at
                    )
                    VALUES (
                        :tenant_id,
                        :job_type,
                        CAST(:payload AS jsonb),
                        'PENDING',
                        0,
                        NOW()
                    )
                    RETURNING id
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "job_type": job_type,
                        "payload": json.dumps(payload),
                    },
                )
                return UUID(str(result.scalar_one()))

        now = datetime.now(UTC)
        job_id = uuid4()
        self._outbox_jobs[job_id] = {
            "id": job_id,
            "tenant_id": norm_tenant,
            "job_type": job_type,
            "payload": payload,
            "status": "PENDING",
            "attempts": 0,
            "next_retry_at": now,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }
        return job_id

    async def mark_outbox_job_completed(
        self,
        job_id: UUID,
        tenant_id: str = "default-tenant",
    ) -> None:
        """Transition an outbox job to COMPLETED within the tenant boundary."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE outbox_jobs
                    SET status = 'COMPLETED',
                        last_error = NULL,
                        updated_at = NOW()
                    WHERE id = :job_id
                      AND tenant_id = :tenant_id
                    """
                )
                await session.execute(
                    stmt,
                    {"job_id": job_id, "tenant_id": norm_tenant},
                )
            return

        job = self._outbox_jobs.get(job_id)
        if job is not None and _normalize_tenant_id(str(job["tenant_id"])) == norm_tenant:
            job["status"] = "COMPLETED"
            job["last_error"] = None
            job["updated_at"] = datetime.now(UTC)

    async def mark_outbox_job_failed(
        self,
        job_id: UUID,
        error_message: str,
        tenant_id: str = "default-tenant",
        max_attempts: int = 5,
    ) -> None:
        """Record an outbox job failure, scheduling exponential retry or DEAD_LETTER."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        effective_max = max(1, int(max_attempts))
        safe_error = _sanitize_outbox_error(error_message)

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE outbox_jobs
                    SET attempts = attempts + 1,
                        status = CASE
                            WHEN attempts + 1 >= :max_attempts THEN 'DEAD_LETTER'
                            ELSE 'FAILED'
                        END,
                        last_error = :last_error,
                        next_retry_at = CASE
                            WHEN attempts + 1 >= :max_attempts THEN NOW()
                            ELSE NOW() + (
                                LEAST(3600, POWER(2, LEAST(attempts + 1, 10)))
                                * INTERVAL '1 second'
                            )
                        END,
                        updated_at = NOW()
                    WHERE id = :job_id
                      AND tenant_id = :tenant_id
                    """
                )
                await session.execute(
                    stmt,
                    {
                        "job_id": job_id,
                        "tenant_id": norm_tenant,
                        "max_attempts": effective_max,
                        "last_error": safe_error,
                    },
                )
            return

        job = self._outbox_jobs.get(job_id)
        if job is not None and _normalize_tenant_id(str(job["tenant_id"])) == norm_tenant:
            now = datetime.now(UTC)
            next_attempts = int(job.get("attempts", 0)) + 1
            is_dead_letter = next_attempts >= effective_max
            backoff_seconds = min(3600, 2 ** min(next_attempts, 10))
            job["attempts"] = next_attempts
            job["status"] = "DEAD_LETTER" if is_dead_letter else "FAILED"
            job["last_error"] = safe_error
            job["next_retry_at"] = (
                now if is_dead_letter else now + timedelta(seconds=backoff_seconds)
            )
            job["updated_at"] = now

    async def list_pending_outbox_jobs(
        self,
        tenant_id: str = "default-tenant",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """List due PENDING/FAILED outbox jobs for the tenant ordered by retry schedule."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        effective_limit = max(1, min(int(limit), 500))

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        id,
                        tenant_id,
                        job_type,
                        payload,
                        status,
                        attempts,
                        next_retry_at,
                        last_error,
                        created_at,
                        updated_at
                    FROM outbox_jobs
                    WHERE tenant_id = :tenant_id
                      AND status IN ('PENDING', 'FAILED')
                      AND next_retry_at <= NOW()
                    ORDER BY next_retry_at ASC, created_at ASC
                    LIMIT :limit
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "limit": effective_limit},
                )
                return [dict(row) for row in result.mappings().all()]

        now = datetime.now(UTC)
        due_jobs = [
            dict(job)
            for job in self._outbox_jobs.values()
            if _normalize_tenant_id(str(job["tenant_id"])) == norm_tenant
            and job.get("status") in ("PENDING", "FAILED")
            and job.get("next_retry_at", now) <= now
        ]
        due_jobs.sort(key=lambda item: (item["next_retry_at"], item["created_at"]))
        return due_jobs[:effective_limit]

    async def list_webhook_logs(
        self, tenant_id: str = "default-tenant", limit: int = 50
    ) -> list[WebhookLogResponse]:
        """List tenant-scoped SePay webhook audit logs ordered newest-first."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        id,
                        sepay_transaction_id,
                        tenant_id,
                        order_code,
                        transfer_type,
                        transfer_amount,
                        account_number,
                        sub_account,
                        reference_code,
                        processing_status,
                        raw_payload,
                        created_at
                    FROM sepay_webhook_logs
                    WHERE tenant_id = :tenant_id
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "limit": limit},
                )
                rows = result.mappings().all()
                return [WebhookLogResponse.model_validate(dict(row)) for row in rows]

        matching = [
            entry
            for entry in self._sepay_webhook_logs.values()
            if _normalize_tenant_id(str(entry["tenant_id"])) == norm_tenant
        ]
        matching.sort(key=lambda item: item["created_at"], reverse=True)
        logs: list[WebhookLogResponse] = []
        for idx, entry in enumerate(matching[:limit], start=1):
            logs.append(
                WebhookLogResponse(
                    id=int(entry.get("id", idx)),
                    sepay_transaction_id=str(entry["sepay_transaction_id"]),
                    tenant_id=str(entry["tenant_id"]),
                    order_code=entry.get("order_code"),
                    transfer_type=str(entry["transfer_type"]),
                    transfer_amount=int(entry["transfer_amount"]),
                    account_number=entry.get("account_number"),
                    sub_account=entry.get("sub_account"),
                    reference_code=entry.get("reference_code"),
                    processing_status=str(entry["processing_status"]),
                    raw_payload=dict(entry.get("raw_payload") or {}),
                    created_at=entry["created_at"],
                )
            )
        return logs

    async def create_user(
        self,
        email: str,
        password_hash: str,
        role: str = "staff",
        tenant_id: str = "default-tenant",
        is_active: bool = True,
    ) -> dict[str, Any]:
        """Create a tenant-scoped operator user account."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_email = email.strip().lower()
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    INSERT INTO users (tenant_id, email, password_hash, role, is_active)
                    VALUES (:tenant_id, :email, :password_hash, :role, :is_active)
                    RETURNING id, tenant_id, email, password_hash, role, is_active, created_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "email": norm_email,
                        "password_hash": password_hash,
                        "role": role,
                        "is_active": is_active,
                    },
                )
                return dict(result.mappings().one())

        for existing in self._users.values():
            if (
                _normalize_tenant_id(str(existing["tenant_id"])) == norm_tenant
                and str(existing["email"]).lower() == norm_email
            ):
                raise ValueError(f"User {norm_email} already exists for tenant {norm_tenant}")

        user_id = uuid4()
        record = {
            "id": user_id,
            "tenant_id": norm_tenant,
            "email": norm_email,
            "password_hash": password_hash,
            "role": role,
            "is_active": is_active,
            "created_at": datetime.now(UTC),
        }
        self._users[user_id] = record
        return dict(record)

    async def get_user_by_email(
        self, email: str, tenant_id: str = "default-tenant"
    ) -> dict[str, Any] | None:
        """Fetch a tenant-scoped user account by email."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_email = email.strip().lower()
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT id, tenant_id, email, password_hash, role, is_active, created_at
                    FROM users
                    WHERE tenant_id = :tenant_id
                      AND LOWER(email) = :email
                    LIMIT 1
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "email": norm_email},
                )
                row = result.mappings().first()
                return dict(row) if row else None

        for user in self._users.values():
            if (
                _normalize_tenant_id(str(user["tenant_id"])) == norm_tenant
                and str(user["email"]).lower() == norm_email
            ):
                return dict(user)
        return None

    async def store_token_record(
        self,
        user_id: UUID | str,
        jti: str,
        token_hash: str,
        token_type: str,
        expires_at: datetime,
        tenant_id: str = "default-tenant",
    ) -> UUID:
        """Persist an issued API token record for JTI revocation and refresh tracking."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    INSERT INTO api_tokens (
                        tenant_id,
                        user_id,
                        jti,
                        token_hash,
                        token_type,
                        expires_at
                    )
                    VALUES (
                        :tenant_id,
                        :user_id,
                        :jti,
                        :token_hash,
                        :token_type,
                        :expires_at
                    )
                    RETURNING id
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "user_id": user_uuid,
                        "jti": jti,
                        "token_hash": token_hash,
                        "token_type": token_type,
                        "expires_at": expires_at,
                    },
                )
                return UUID(str(result.scalar_one()))

        token_id = uuid4()
        self._api_tokens[jti] = {
            "id": token_id,
            "tenant_id": norm_tenant,
            "user_id": user_uuid,
            "jti": jti,
            "token_hash": token_hash,
            "token_type": token_type,
            "expires_at": expires_at,
            "revoked_at": None,
            "created_at": datetime.now(UTC),
        }
        return token_id

    async def is_token_revoked(
        self, jti: str, tenant_id: str = "default-tenant"
    ) -> bool:
        """Return True if the token JTI has been explicitly revoked within the tenant."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT revoked_at
                    FROM api_tokens
                    WHERE tenant_id = :tenant_id
                      AND jti = :jti
                    LIMIT 1
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "jti": jti},
                )
                row = result.first()
                if row is None:
                    return False
                return row[0] is not None

        record = self._api_tokens.get(jti)
        if record is None or _normalize_tenant_id(str(record["tenant_id"])) != norm_tenant:
            return False
        return record.get("revoked_at") is not None

    async def revoke_token_jti(
        self, jti: str, tenant_id: str = "default-tenant"
    ) -> bool:
        """Revoke an active token JTI inside the tenant; returns True if revoked now."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE api_tokens
                    SET revoked_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND jti = :jti
                      AND revoked_at IS NULL
                    RETURNING id
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "jti": jti},
                )
                return result.scalar_one_or_none() is not None

        record = self._api_tokens.get(jti)
        if (
            record is None
            or _normalize_tenant_id(str(record["tenant_id"])) != norm_tenant
            or record.get("revoked_at") is not None
        ):
            return False
        record["revoked_at"] = datetime.now(UTC)
        return True


InMemoryERPStore = ERPRepository
erp_repository = ERPRepository()
