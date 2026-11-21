"""Async SQLAlchemy + asyncpg database layer, RLS session context, and pgvector `<=>` cosine search."""

from __future__ import annotations

import json
import logging
import math
import re
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

from app.core.config import (
    get_runtime_database_url,
    get_settings,
    is_production_env,
    is_running_under_pytest,
    is_test_postgres_enabled,
)
from app.models.entities import (
    ChannelConversationResponse,
    ChannelMessageResponse,
    CompanyResponse,
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

settings = get_settings()

_use_null_pool = is_test_postgres_enabled() or is_running_under_pytest()


def _build_engine(db_url: str) -> AsyncEngine | None:
    cleaned = db_url.strip()
    if not cleaned:
        return None
    if _use_null_pool:
        return create_async_engine(
            cleaned,
            echo=False,
            poolclass=NullPool,
            future=True,
            connect_args={"statement_cache_size": 0},
        )
    return create_async_engine(
        cleaned,
        echo=False,
        pool_pre_ping=True,
        future=True,
        connect_args={"statement_cache_size": 0},
    )


# SQLAlchemy 2.0 AsyncEngine configured for Supabase PostgreSQL + Supavisor pooling
engine: AsyncEngine | None = _build_engine(settings.database_url)

AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = (
    async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    if engine is not None
    else None
)

# Canonical SQL query exercising Supabase `pgvector` `<=>` cosine distance operator
PGVECTOR_SIMILARITY_SQL = text(
    """
    SELECT
        id,
        tenant_id,
        company_id,
        sku,
        name,
        description,
        category,
        price_vnd,
        stock_quantity,
        is_active,
        deactivated_at,
        (embedding <=> CAST(:query_embedding AS vector(768))) AS distance,
        1.0 - (embedding <=> CAST(:query_embedding AS vector(768))) AS similarity_score,
        created_at,
        updated_at
    FROM inventory
    WHERE tenant_id = :tenant_id
      AND is_active = TRUE
      AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:query_embedding AS vector(768)) ASC
    LIMIT :limit
    """
)


def _is_production_env() -> bool:
    """Return True when running in production mode where DB fallback is forbidden."""
    return is_production_env()


def _normalize_tenant_id(tenant_id: str) -> str:
    """Validate and normalize a tenant identifier, failing closed on empty values."""
    if not isinstance(tenant_id, str):
        raise ValueError("tenant_id must be a non-empty string")
    cleaned = tenant_id.strip()
    if not cleaned:
        raise ValueError("tenant_id must not be empty")
    return cleaned


def _resolve_repo_tenant(tenant_id: str | None) -> str:
    """Resolve explicit tenant_id or configured settings.default_tenant_id."""
    if tenant_id is not None:
        return _normalize_tenant_id(tenant_id)
    configured = get_settings().default_tenant_id.strip()
    if not configured:
        raise ValueError("tenant_id is required when DEFAULT_TENANT_ID is not configured")
    return _normalize_tenant_id(configured)


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return active async_sessionmaker or initialize lazily from runtime DATABASE_URL."""
    global engine, AsyncSessionLocal
    if AsyncSessionLocal is not None:
        return AsyncSessionLocal
    runtime_url = get_runtime_database_url()
    if not runtime_url:
        raise RuntimeError("DATABASE_URL is not configured")
    engine = _build_engine(runtime_url)
    assert engine is not None
    AsyncSessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return AsyncSessionLocal


@asynccontextmanager
async def tenant_db_session(
    tenant_id: str,
    company_id: UUID | str | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Open an explicit transaction and bind `app.tenant_id` and optional `app.company_id` for RLS."""
    norm_tenant = _normalize_tenant_id(tenant_id)
    company_str = str(company_id).strip() if company_id is not None else ""
    session_factory = _get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": norm_tenant},
            )
            await session.execute(
                text("SELECT set_config('app.company_id', :company_id, true)"),
                {"company_id": company_str},
            )
            yield session


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async SQLAlchemy session."""
    session_factory = _get_session_factory()
    async with session_factory() as session:
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
        self._companies: dict[UUID, CompanyResponse] = {}
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
        self._channel_conversations: dict[UUID, ChannelConversationResponse] = {}
        self._channel_messages: dict[UUID, ChannelMessageResponse] = {}
        self._order_code_counter: int = 1001
        self._seed_defaults(datetime.now(UTC))

    def reset(self) -> None:
        """Reset repository state and re-seed deterministic fixture defaults for test isolation."""
        self._companies.clear()
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
        self._channel_conversations.clear()
        self._channel_messages.clear()
        self._order_code_counter = 1001
        self._seed_defaults(datetime.now(UTC))

    def _seed_defaults(self, now: datetime) -> None:
        cfg = get_settings()
        seed_tenant = cfg.default_tenant_id.strip()
        if not seed_tenant or cfg.app_env == "production":
            return

        customer_id = UUID("11111111-1111-1111-1111-111111111111")
        employee_id = UUID("22222222-2222-2222-2222-222222222222")

        self._customers[customer_id] = CustomerResponse(
            id=customer_id,
            tenant_id=seed_tenant,
            meta_psid="psid_vn_customer_001",
            full_name="Nguyen Minh Anh",
            phone="+84901234567",
            is_active=True,
            deactivated_at=None,
            created_at=now,
        )

        self._employees[employee_id] = EmployeeResponse(
            id=employee_id,
            tenant_id=seed_tenant,
            full_name="Tran Hoang Nam (Warehouse Lead)",
            role="fulfillment_specialist",
            telegram_user_id="8499887766",
            is_active=True,
            deactivated_at=None,
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
                "tenant_id": seed_tenant,
                "sku": sku,
                "name": name,
                "description": desc,
                "category": category,
                "price_vnd": price,
                "stock_quantity": stock,
                "embedding": None,
                "is_active": True,
                "deactivated_at": None,
                "created_at": now,
                "updated_at": now,
            }

        initial_order_id = UUID("44444444-4444-4444-4444-444444444401")
        qr_url: str | None = None
        if cfg.sepay_qr_base_url and cfg.sepay_account_number and cfg.sepay_bank_code:
            qr_url = (
                f"{cfg.sepay_qr_base_url.rstrip('/')}?"
                f"acc={cfg.sepay_account_number}&bank={cfg.sepay_bank_code}&amount=640000&des=DH1001"
            )
        self._orders[initial_order_id] = OrderResponse(
            id=initial_order_id,
            tenant_id=seed_tenant,
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
            is_active=True,
            deactivated_at=None,
            created_at=now,
            updated_at=now,
        )

    async def is_postgres_active(self) -> bool:
        """Return True when PostgreSQL is configured and reachable; fail closed in production."""
        current_url = get_runtime_database_url() or settings.database_url.strip()
        is_prod = _is_production_env()
        force_pg_tests = is_test_postgres_enabled()

        if not current_url or (is_running_under_pytest() and not force_pg_tests):
            if is_prod and not current_url:
                raise RuntimeError(
                    "PostgreSQL DATABASE_URL must be configured in production mode (fail-closed)"
                )
            if not is_prod:
                return False

        try:
            session_factory = (
                AsyncSessionLocal if AsyncSessionLocal is not None else _get_session_factory()
            )
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            if is_prod:
                logger.error("PostgreSQL unavailable in production mode: %s", exc)
                raise RuntimeError("PostgreSQL database unavailable in production mode") from exc
            logger.warning("PostgreSQL unreachable in non-production mode; using local fallback: %s", exc)
            return False

    # --------------------------------------------------------------------------
    # Synchronous Repository Methods
    # --------------------------------------------------------------------------

    def list_inventory(
        self,
        tenant_id: str | None = None,
        *,
        company_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[InventoryResponse]:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        results: list[InventoryResponse] = []
        for raw in self._inventory.values():
            if _normalize_tenant_id(raw["tenant_id"]) != norm_tenant:
                continue
            if company_id is not None and raw.get("company_id") != company_id:
                continue
            if not include_inactive and not bool(raw.get("is_active", True)):
                continue
            results.append(
                InventoryResponse(
                    id=raw["id"],
                    tenant_id=raw["tenant_id"],
                    company_id=raw.get("company_id"),
                    sku=raw["sku"],
                    name=raw["name"],
                    description=raw["description"],
                    category=raw.get("category", "Hardware"),
                    price_vnd=raw["price_vnd"],
                    stock_quantity=raw["stock_quantity"],
                    has_embedding=raw["embedding"] is not None,
                    is_active=bool(raw.get("is_active", True)),
                    deactivated_at=raw.get("deactivated_at"),
                    similarity_distance=None,
                    similarity_score=None,
                    created_at=raw["created_at"],
                    updated_at=raw["updated_at"],
                )
            )
        return results

    def create_inventory_item(
        self,
        payload: InventoryCreateRequest,
        embedding: list[float],
        company_id: UUID | None = None,
    ) -> InventoryResponse:
        now = datetime.now(UTC)
        item_id = uuid4()
        norm_tenant = _resolve_repo_tenant(payload.tenant_id)
        eff_company = company_id if company_id is not None else payload.company_id
        record = {
            "id": item_id,
            "tenant_id": norm_tenant,
            "company_id": eff_company,
            "sku": payload.sku,
            "name": payload.name,
            "description": payload.description,
            "category": payload.category,
            "price_vnd": payload.price_vnd,
            "stock_quantity": payload.stock_quantity,
            "embedding": embedding,
            "is_active": True,
            "deactivated_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._inventory[item_id] = record
        return InventoryResponse(
            id=item_id,
            tenant_id=norm_tenant,
            company_id=eff_company,
            sku=payload.sku,
            name=payload.name,
            description=payload.description,
            category=payload.category,
            price_vnd=payload.price_vnd,
            stock_quantity=payload.stock_quantity,
            has_embedding=bool(embedding),
            is_active=True,
            deactivated_at=None,
            similarity_distance=None,
            similarity_score=None,
            created_at=now,
            updated_at=now,
        )

    def search_inventory_by_embedding(
        self,
        query_embedding: list[float],
        query_text: str,
        tenant_id: str | None = None,
        limit: int = 5,
        embedding_fn: Any | None = None,
        *,
        company_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[InventoryResponse]:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        scored: list[tuple[float, dict[str, Any]]] = []
        query_terms = set(query_text.lower().split())

        for raw in self._inventory.values():
            if _normalize_tenant_id(raw["tenant_id"]) != norm_tenant:
                continue
            if company_id is not None and raw.get("company_id") != company_id:
                continue
            if not include_inactive and not bool(raw.get("is_active", True)):
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
                    company_id=raw.get("company_id"),
                    sku=raw["sku"],
                    name=raw["name"],
                    description=raw["description"],
                    category=raw.get("category", "Hardware"),
                    price_vnd=raw["price_vnd"],
                    stock_quantity=raw["stock_quantity"],
                    has_embedding=raw["embedding"] is not None,
                    is_active=bool(raw.get("is_active", True)),
                    deactivated_at=raw.get("deactivated_at"),
                    similarity_distance=sim_dist,
                    similarity_score=sim_score,
                    created_at=raw["created_at"],
                    updated_at=raw["updated_at"],
                )
            )
        return results

    search_inventory_cosine = search_inventory_by_embedding

    def list_customers(
        self,
        tenant_id: str | None = None,
        *,
        company_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[CustomerResponse]:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        return [
            c
            for c in self._customers.values()
            if _normalize_tenant_id(c.tenant_id) == norm_tenant
            and (company_id is None or c.company_id == company_id)
            and (include_inactive or c.is_active)
        ]

    def get_or_create_customer_by_psid(
        self,
        meta_psid: str,
        full_name: str = "Meta Shopper",
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> CustomerResponse:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        for cid, customer in self._customers.items():
            if (
                customer.meta_psid == meta_psid
                and _normalize_tenant_id(customer.tenant_id) == norm_tenant
            ):
                updates: dict[str, Any] = {}
                if not customer.is_active:
                    updates["is_active"] = True
                    updates["deactivated_at"] = None
                if company_id is not None and customer.company_id is None:
                    updates["company_id"] = company_id
                if updates:
                    updated = customer.model_copy(update=updates)
                    self._customers[cid] = updated
                    return updated
                return customer
        new_id = uuid4()
        customer = CustomerResponse(
            id=new_id,
            tenant_id=norm_tenant,
            company_id=company_id,
            meta_psid=meta_psid,
            full_name=full_name,
            phone=None,
            is_active=True,
            deactivated_at=None,
            created_at=datetime.now(UTC),
        )
        self._customers[new_id] = customer
        return customer

    def list_employees(
        self,
        tenant_id: str | None = None,
        *,
        company_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[EmployeeResponse]:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        return [
            e
            for e in self._employees.values()
            if _normalize_tenant_id(e.tenant_id) == norm_tenant
            and (company_id is None or e.company_id == company_id)
            and (include_inactive or e.is_active)
        ]

    def get_employee(
        self,
        employee_id: UUID | None,
        tenant_id: str | None = None,
        *,
        company_id: UUID | None = None,
    ) -> EmployeeResponse | None:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if employee_id and employee_id in self._employees:
            emp = self._employees[employee_id]
            if (
                _normalize_tenant_id(emp.tenant_id) == norm_tenant
                and emp.is_active
                and (company_id is None or emp.company_id == company_id)
            ):
                return emp
        # Fallback to first active employee in the tenant (and company if specified)
        for emp in self._employees.values():
            if (
                emp.is_active
                and _normalize_tenant_id(emp.tenant_id) == norm_tenant
                and (company_id is None or emp.company_id == company_id)
            ):
                return emp
        return None

    def list_orders(
        self,
        tenant_id: str | None = None,
        *,
        company_id: UUID | None = None,
        assigned_employee_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[OrderResponse]:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        return [
            o
            for o in self._orders.values()
            if _normalize_tenant_id(o.tenant_id) == norm_tenant
            and (company_id is None or o.company_id == company_id)
            and (assigned_employee_id is None or o.assigned_employee_id == assigned_employee_id)
            and (include_inactive or o.is_active)
        ]

    def create_order(
        self,
        payload: OrderCreateRequest,
        sepay_qr_url: str,
        order_code: str,
        company_id: UUID | None = None,
        company_name: str = "",
    ) -> OrderResponse:
        now = datetime.now(UTC)
        order_id = uuid4()
        norm_tenant = _resolve_repo_tenant(payload.tenant_id)
        eff_company = company_id if company_id is not None else payload.company_id
        if eff_company is not None and not company_name:
            comp = self._companies.get(eff_company)
            if comp is not None and _normalize_tenant_id(comp.tenant_id) == norm_tenant:
                company_name = comp.name

        resolved_items: list[OrderLineItem] = []
        for item in payload.items:
            inv_record = next(
                (
                    raw
                    for raw in self._inventory.values()
                    if _normalize_tenant_id(str(raw["tenant_id"])) == norm_tenant
                    and (eff_company is None or raw.get("company_id") == eff_company)
                    and bool(raw.get("is_active", True))
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
            default_emp = self.get_employee(
                None,
                tenant_id=norm_tenant,
                company_id=eff_company,
            )
            assigned_emp_id = default_emp.id if default_emp else None

        customer = self._customers.get(payload.customer_id)
        customer_name = customer.full_name if customer else "Enterprise Customer"
        items_dump = [item.model_dump() for item in resolved_items]

        order = OrderResponse(
            id=order_id,
            tenant_id=norm_tenant,
            company_id=eff_company,
            company_name=company_name,
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
            is_active=True,
            deactivated_at=None,
            created_at=now,
            updated_at=now,
        )
        self._orders[order_id] = order
        return order

    def update_order_status(
        self,
        order_id: UUID,
        new_status: OrderStatus,
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> OrderResponse | None:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        existing = self._orders.get(order_id)
        if (
            not existing
            or not existing.is_active
            or _normalize_tenant_id(existing.tenant_id) != norm_tenant
            or (company_id is not None and existing.company_id != company_id)
        ):
            return None
        updated = existing.model_copy(
            update={"status": new_status, "updated_at": datetime.now(UTC)}
        )
        self._orders[order_id] = updated
        return updated

    def get_order_by_code(
        self,
        order_code: str,
        tenant_id: str | None = None,
        *,
        company_id: UUID | None = None,
    ) -> OrderResponse | None:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        normalized_code = order_code.upper().strip()
        for existing in self._orders.values():
            if (
                existing.is_active
                and existing.order_code.upper() == normalized_code
                and _normalize_tenant_id(existing.tenant_id) == norm_tenant
                and (company_id is None or existing.company_id == company_id)
            ):
                return existing
        return None

    def mark_order_paid_by_code(
        self,
        order_code: str,
        transaction_id: str,
        tenant_id: str | None = None,
        *,
        company_id: UUID | None = None,
    ) -> OrderResponse | None:
        norm_tenant = _resolve_repo_tenant(tenant_id)
        normalized_code = order_code.upper().strip()
        for order_id, existing in self._orders.items():
            if (
                existing.is_active
                and existing.order_code.upper() == normalized_code
                and _normalize_tenant_id(existing.tenant_id) == norm_tenant
                and (company_id is None or existing.company_id == company_id)
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
        tenant_id: str | None = None,
        account_number: str | None = None,
        sub_account: str | None = None,
        reference_code: str | None = None,
        company_id: UUID | None = None,
    ) -> bool:
        """Atomically record a SePay transaction log in memory; returns False if already recorded."""
        if transaction_id in self._sepay_webhook_logs:
            return False
        self._sepay_webhook_logs[transaction_id] = {
            "id": len(self._sepay_webhook_logs) + 1,
            "sepay_transaction_id": transaction_id,
            "tenant_id": _resolve_repo_tenant(tenant_id),
            "company_id": company_id,
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
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> bool:
        """Persist SePay webhook deduplication log and PAID transition to PostgreSQL when active.

        Returns False only if PostgreSQL already holds this `sepay_transaction_id` (duplicate).
        """
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if not await self.is_postgres_active():
            return True

        try:
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                insert_stmt = text(
                    """
                    INSERT INTO sepay_webhook_logs (
                        sepay_transaction_id,
                        tenant_id,
                        company_id,
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
                        :company_id,
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
                        "company_id": company_id,
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
                          AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                          AND is_active = TRUE
                          AND status = 'PENDING_PAYMENT'
                        """
                    )
                    await session.execute(
                        update_stmt,
                        {
                            "tx_id": transaction_id,
                            "order_code": order_code.upper().strip(),
                            "tenant_id": norm_tenant,
                            "company_id": company_id,
                        },
                    )
                return True
        except Exception as exc:  # pragma: no cover - propagate DB errors so SePay retries
            logger.error("PostgreSQL SePay persistence failed: %s", exc)
            raise

    async def resolve_channel_context(
        self,
        channel_type: str,
        external_id: str,
        sub_account: str = "",
    ) -> tuple[str, UUID | None] | None:
        """Resolve `(tenant_id, company_id)` for an inbound webhook channel before RLS context is set."""
        norm_ext = external_id.strip()
        if not norm_ext:
            return None
        norm_sub = (sub_account or "").strip()
        if await self.is_postgres_active():
            session_factory = (
                AsyncSessionLocal if AsyncSessionLocal is not None else _get_session_factory()
            )
            async with session_factory() as session:
                async with session.begin():
                    stmt = text(
                        "SELECT tenant_id, company_id "
                        "FROM resolve_channel_context(:channel_type, :external_id, :sub_account)"
                    )
                    result = await session.execute(
                        stmt,
                        {
                            "channel_type": channel_type,
                            "external_id": norm_ext,
                            "sub_account": norm_sub,
                        },
                    )
                    row = result.first()
                    if row is None and norm_sub:
                        result = await session.execute(
                            stmt,
                            {
                                "channel_type": channel_type,
                                "external_id": norm_ext,
                                "sub_account": "",
                            },
                        )
                        row = result.first()
                    if row is None:
                        return None
                    cid = UUID(str(row[1])) if row[1] is not None else None
                    return (str(row[0]), cid)

        record = self._tenant_channels.get((channel_type, norm_ext, norm_sub))
        if (record is None or not record.get("is_active", True)) and norm_sub:
            record = self._tenant_channels.get((channel_type, norm_ext, ""))
        if record and record.get("is_active", True):
            raw_cid = record.get("company_id")
            cid = UUID(str(raw_cid)) if raw_cid is not None else None
            return (str(record["tenant_id"]), cid)
        return None

    async def resolve_channel_tenant(
        self,
        channel_type: str,
        external_id: str,
        sub_account: str = "",
    ) -> str | None:
        """Resolve owning `tenant_id` for an inbound webhook channel before RLS context is set."""
        ctx = await self.resolve_channel_context(
            channel_type=channel_type,
            external_id=external_id,
            sub_account=sub_account,
        )
        return ctx[0] if ctx is not None else None

    async def register_company_channel(
        self,
        *,
        tenant_id: str,
        company_id: UUID,
        channel_type: str,
        external_id: str,
        sub_account: str = "",
        session: AsyncSession | None = None,
    ) -> None:
        """Register or update a company-bound `tenant_channels` routing entry without overwriting another company."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_ext = external_id.strip()
        norm_sub = (sub_account or "").strip()
        if not norm_ext:
            return

        if session is not None or await self.is_postgres_active():
            stmt = text(
                """
                INSERT INTO tenant_channels (
                    tenant_id, company_id, channel_type, external_id, sub_account, is_active
                )
                VALUES (
                    :tenant_id, :company_id, :channel_type, :external_id, :sub_account, TRUE
                )
                ON CONFLICT (channel_type, external_id, sub_account) DO UPDATE
                    SET company_id = EXCLUDED.company_id,
                        is_active = TRUE
                WHERE tenant_channels.tenant_id = EXCLUDED.tenant_id
                  AND (
                      tenant_channels.company_id IS NULL
                      OR tenant_channels.company_id = EXCLUDED.company_id
                  )
                RETURNING id
                """
            )
            params = {
                "tenant_id": norm_tenant,
                "company_id": company_id,
                "channel_type": channel_type,
                "external_id": norm_ext,
                "sub_account": norm_sub,
            }
            if session is not None:
                res = await session.execute(stmt, params)
                if res.scalar_one_or_none() is None:
                    raise ValueError(
                        f"Channel {channel_type}:{norm_ext} is already bound to another company or tenant"
                    )
                return

            async with tenant_db_session(norm_tenant, company_id=company_id) as db_session:
                res = await db_session.execute(stmt, params)
                if res.scalar_one_or_none() is None:
                    raise ValueError(
                        f"Channel {channel_type}:{norm_ext} is already bound to another company or tenant"
                    )
            return

        key = (channel_type, norm_ext, norm_sub)
        existing = self._tenant_channels.get(key)
        if existing is not None:
            if _normalize_tenant_id(str(existing["tenant_id"])) != norm_tenant:
                raise ValueError(
                    f"Channel {channel_type}:{norm_ext} is already bound to another tenant"
                )
            existing_cid = existing.get("company_id")
            if existing_cid is not None and existing_cid != company_id:
                raise ValueError(
                    f"Channel {channel_type}:{norm_ext} is already bound to another company"
                )
        self._tenant_channels[key] = {
            "id": existing["id"] if existing else uuid4(),
            "tenant_id": norm_tenant,
            "company_id": company_id,
            "channel_type": channel_type,
            "external_id": norm_ext,
            "sub_account": norm_sub,
            "is_active": True,
            "created_at": existing["created_at"] if existing else datetime.now(UTC),
        }

    async def next_order_code(
        self, tenant_id: str | None = None, prefix: str = "DH"
    ) -> str:
        """Generate a globally monotonic order code backed by `order_code_seq`."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> bool:
        """Atomically decrement active inventory stock for all order line items; roll back if any is short."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        parsed = sorted((_extract_sku_qty(item) for item in items), key=lambda pair: pair[0])
        if not parsed:
            return True

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                update_stmt = text(
                    """
                    UPDATE inventory
                    SET stock_quantity = stock_quantity - :qty,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                      AND sku = :sku
                      AND is_active = TRUE
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
                        {
                            "tenant_id": norm_tenant,
                            "company_id": company_id,
                            "sku": sku,
                            "qty": qty,
                        },
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
                    if _normalize_tenant_id(raw["tenant_id"]) == norm_tenant
                    and (company_id is None or raw.get("company_id") == company_id)
                    and bool(raw.get("is_active", True))
                    and raw["sku"] == sku
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
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> None:
        """Restore previously decremented stock quantities (e.g., on order cancellation)."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        parsed = sorted((_extract_sku_qty(item) for item in items), key=lambda pair: pair[0])
        if not parsed:
            return

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                update_stmt = text(
                    """
                    UPDATE inventory
                    SET stock_quantity = stock_quantity + :qty,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                      AND sku = :sku
                    """
                )
                for sku, qty in parsed:
                    if qty > 0:
                        await session.execute(
                            update_stmt,
                            {
                                "tenant_id": norm_tenant,
                                "company_id": company_id,
                                "sku": sku,
                                "qty": qty,
                            },
                        )
            return

        now = datetime.now(UTC)
        for sku, qty in parsed:
            if qty <= 0:
                continue
            for raw in self._inventory.values():
                if (
                    _normalize_tenant_id(raw["tenant_id"]) == norm_tenant
                    and (company_id is None or raw.get("company_id") == company_id)
                    and raw["sku"] == sku
                ):
                    raw["stock_quantity"] = int(raw["stock_quantity"]) + qty
                    raw["updated_at"] = now

    async def record_webhook_dedup(
        self,
        provider: str,
        external_event_id: str,
        payload_hash: str,
        tenant_id: str | None = None,
    ) -> bool:
        """Record an inbound webhook event idempotently; returns False on duplicate delivery."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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
        tenant_id: str | None = None,
    ) -> UUID:
        """Enqueue a transactional outbox job for reliable async downstream dispatch."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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
        tenant_id: str | None = None,
    ) -> None:
        """Transition an outbox job to COMPLETED within the tenant boundary."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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
        tenant_id: str | None = None,
        max_attempts: int = 5,
    ) -> None:
        """Record an outbox job failure, scheduling exponential retry or DEAD_LETTER."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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
        tenant_id: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """List due PENDING/FAILED outbox jobs for the tenant ordered by retry schedule."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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
        self,
        tenant_id: str | None = None,
        limit: int = 50,
        company_id: UUID | None = None,
    ) -> list[WebhookLogResponse]:
        """List tenant-scoped (and optionally company-scoped) SePay webhook audit logs ordered newest-first."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    SELECT
                        id,
                        sepay_transaction_id,
                        tenant_id,
                        company_id,
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
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "company_id": company_id, "limit": limit},
                )
                rows = result.mappings().all()
                return [WebhookLogResponse.model_validate(dict(row)) for row in rows]

        matching = [
            entry
            for entry in self._sepay_webhook_logs.values()
            if _normalize_tenant_id(str(entry["tenant_id"])) == norm_tenant
            and (company_id is None or entry.get("company_id") == company_id)
        ]
        matching.sort(key=lambda item: item["created_at"], reverse=True)
        logs: list[WebhookLogResponse] = []
        for idx, entry in enumerate(matching[:limit], start=1):
            logs.append(
                WebhookLogResponse(
                    id=int(entry.get("id", idx)),
                    sepay_transaction_id=str(entry["sepay_transaction_id"]),
                    tenant_id=str(entry["tenant_id"]),
                    company_id=entry.get("company_id"),
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
        tenant_id: str | None = None,
        is_active: bool = True,
        *,
        username: str | None = None,
        full_name: str = "",
        company_id: UUID | None = None,
        employee_id: UUID | None = None,
        must_change_password: bool = False,
    ) -> dict[str, Any]:
        """Create a tenant-scoped operator, manager, or staff user account."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        norm_email = email.strip().lower()
        norm_username = username.strip() if username and username.strip() else None
        norm_role = role.strip().lower()

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    INSERT INTO users (
                        tenant_id, email, username, full_name, password_hash, role,
                        company_id, employee_id, must_change_password, is_active
                    )
                    VALUES (
                        :tenant_id, :email, :username, :full_name, :password_hash, :role,
                        :company_id, :employee_id, :must_change_password, :is_active
                    )
                    RETURNING
                        id, tenant_id, email, username, full_name, password_hash, role,
                        company_id, employee_id, must_change_password, password_changed_at,
                        is_active, created_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "email": norm_email,
                        "username": norm_username,
                        "full_name": full_name.strip(),
                        "password_hash": password_hash,
                        "role": norm_role,
                        "company_id": company_id,
                        "employee_id": employee_id,
                        "must_change_password": must_change_password,
                        "is_active": is_active,
                    },
                )
                return dict(result.mappings().one())

        for existing in self._users.values():
            if _normalize_tenant_id(str(existing["tenant_id"])) != norm_tenant:
                continue
            if str(existing["email"]).lower() == norm_email:
                raise ValueError(f"User {norm_email} already exists for tenant {norm_tenant}")
            if (
                norm_username is not None
                and existing.get("username")
                and str(existing["username"]).lower() == norm_username.lower()
            ):
                raise ValueError(f"Username {norm_username} already exists for tenant {norm_tenant}")

        user_id = uuid4()
        record = {
            "id": user_id,
            "tenant_id": norm_tenant,
            "email": norm_email,
            "username": norm_username,
            "full_name": full_name.strip(),
            "password_hash": password_hash,
            "role": norm_role,
            "company_id": company_id,
            "employee_id": employee_id,
            "must_change_password": must_change_password,
            "password_changed_at": None,
            "is_active": is_active,
            "created_at": datetime.now(UTC),
        }
        self._users[user_id] = record
        return dict(record)

    async def tenant_has_users(self, tenant_id: str) -> bool:
        """Return True if at least one user account already exists for `tenant_id`."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT 1
                    FROM users
                    WHERE tenant_id = :tenant_id
                    LIMIT 1
                    """
                )
                result = await session.execute(stmt, {"tenant_id": norm_tenant})
                return result.scalar_one_or_none() is not None

        return any(
            _normalize_tenant_id(str(user["tenant_id"])) == norm_tenant
            for user in self._users.values()
        )

    async def get_user_by_email(
        self, email: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch a tenant-scoped user account by email."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        norm_email = email.strip().lower()
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        id, tenant_id, email, username, full_name, password_hash, role,
                        company_id, employee_id, must_change_password, password_changed_at,
                        is_active, created_at
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

    async def get_user_by_identifier(
        self, identifier: str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch a tenant-scoped user account by email OR username."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        norm_ident = identifier.strip().lower()
        if not norm_ident:
            return None
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        id, tenant_id, email, username, full_name, password_hash, role,
                        company_id, employee_id, must_change_password, password_changed_at,
                        is_active, created_at
                    FROM users
                    WHERE tenant_id = :tenant_id
                      AND (
                          LOWER(email) = :ident
                          OR (username IS NOT NULL AND LOWER(username) = :ident)
                      )
                    LIMIT 1
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "ident": norm_ident},
                )
                row = result.mappings().first()
                return dict(row) if row else None

        for user in self._users.values():
            if _normalize_tenant_id(str(user["tenant_id"])) != norm_tenant:
                continue
            email_match = str(user["email"]).lower() == norm_ident
            uname = user.get("username")
            uname_match = bool(uname and str(uname).lower() == norm_ident)
            if email_match or uname_match:
                return dict(user)
        return None

    async def get_user_by_id(
        self, user_id: UUID | str, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch a tenant-scoped user account by primary ID."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        try:
            user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        except ValueError:
            return None

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        id, tenant_id, email, username, full_name, password_hash, role,
                        company_id, employee_id, must_change_password, password_changed_at,
                        is_active, created_at
                    FROM users
                    WHERE tenant_id = :tenant_id
                      AND id = :user_id
                    LIMIT 1
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "user_id": user_uuid},
                )
                row = result.mappings().first()
                return dict(row) if row else None

        user = self._users.get(user_uuid)
        if user is None or _normalize_tenant_id(str(user["tenant_id"])) != norm_tenant:
            return None
        return dict(user)

    async def update_user_password(
        self,
        user_id: UUID | str,
        password_hash: str,
        tenant_id: str | None = None,
        *,
        must_change_password: bool = False,
    ) -> dict[str, Any] | None:
        """Update a user's password hash and password rotation state."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        try:
            user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        except ValueError:
            return None

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE users
                    SET password_hash = :password_hash,
                        must_change_password = :must_change_password,
                        password_changed_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND id = :user_id
                    RETURNING
                        id, tenant_id, email, username, full_name, password_hash, role,
                        company_id, employee_id, must_change_password, password_changed_at,
                        is_active, created_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "user_id": user_uuid,
                        "password_hash": password_hash,
                        "must_change_password": must_change_password,
                    },
                )
                row = result.mappings().first()
                return dict(row) if row else None

        user = self._users.get(user_uuid)
        if user is None or _normalize_tenant_id(str(user["tenant_id"])) != norm_tenant:
            return None
        user["password_hash"] = password_hash
        user["must_change_password"] = must_change_password
        user["password_changed_at"] = datetime.now(UTC)
        return dict(user)

    async def store_token_record(
        self,
        user_id: UUID | str,
        jti: str,
        token_hash: str,
        token_type: str,
        expires_at: datetime,
        tenant_id: str | None = None,
    ) -> UUID:
        """Persist an issued API token record for JTI revocation and refresh tracking."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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
        self, jti: str, tenant_id: str | None = None
    ) -> bool:
        """Return True if the token JTI has been explicitly revoked within the tenant."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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
        self, jti: str, tenant_id: str | None = None
    ) -> bool:
        """Revoke an active token JTI inside the tenant; returns True if revoked now."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
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

    async def revoke_all_user_tokens(
        self,
        user_id: UUID | str,
        tenant_id: str | None = None,
    ) -> int:
        """Revoke all active access & refresh tokens for `user_id` inside `tenant_id`."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE api_tokens
                    SET revoked_at = COALESCE(revoked_at, NOW())
                    WHERE tenant_id = :tenant_id
                      AND user_id = :user_id
                      AND revoked_at IS NULL
                    RETURNING id
                    """
                )
                result = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "user_id": user_uuid},
                )
                return len(result.fetchall())

        now = datetime.now(UTC)
        revoked_count = 0
        for record in self._api_tokens.values():
            if (
                _normalize_tenant_id(str(record["tenant_id"])) == norm_tenant
                and record.get("user_id") == user_uuid
                and record.get("revoked_at") is None
            ):
                record["revoked_at"] = now
                revoked_count += 1
        return revoked_count

    # --------------------------------------------------------------------------
    # Soft-Delete, Restore & Retention Purge (PostgreSQL + In-Memory Parity)
    # --------------------------------------------------------------------------

    @staticmethod
    def _inventory_response_from_raw(raw: dict[str, Any]) -> InventoryResponse:
        return InventoryResponse(
            id=raw["id"],
            tenant_id=str(raw["tenant_id"]),
            company_id=raw.get("company_id"),
            sku=str(raw["sku"]),
            name=str(raw["name"]),
            description=str(raw["description"]),
            category=str(raw.get("category", "Hardware")),
            price_vnd=int(raw["price_vnd"]),
            stock_quantity=int(raw["stock_quantity"]),
            has_embedding=bool(raw.get("has_embedding", raw.get("embedding") is not None)),
            is_active=bool(raw.get("is_active", True)),
            deactivated_at=raw.get("deactivated_at"),
            similarity_distance=None,
            similarity_score=None,
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
        )

    async def soft_delete_inventory_item(
        self,
        item_id: UUID,
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> InventoryResponse | None:
        """Soft-delete an inventory item (`is_active=False`, `deactivated_at=NOW()`)."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    UPDATE inventory
                    SET is_active = FALSE,
                        deactivated_at = COALESCE(deactivated_at, NOW()),
                        updated_at = NOW()
                    WHERE id = :item_id
                      AND tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    RETURNING
                        id,
                        tenant_id,
                        company_id,
                        sku,
                        name,
                        description,
                        category,
                        price_vnd,
                        stock_quantity,
                        (embedding IS NOT NULL) AS has_embedding,
                        is_active,
                        deactivated_at,
                        created_at,
                        updated_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {"item_id": item_id, "tenant_id": norm_tenant, "company_id": company_id},
                )
                row = result.mappings().first()
                return InventoryResponse.model_validate(dict(row)) if row else None

        raw = self._inventory.get(item_id)
        if (
            raw is None
            or _normalize_tenant_id(str(raw["tenant_id"])) != norm_tenant
            or (company_id is not None and raw.get("company_id") != company_id)
        ):
            return None
        now = datetime.now(UTC)
        raw["is_active"] = False
        raw["deactivated_at"] = raw.get("deactivated_at") or now
        raw["updated_at"] = now
        return self._inventory_response_from_raw(raw)

    async def restore_inventory_item(
        self,
        item_id: UUID,
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> InventoryResponse | None:
        """Restore a soft-deleted inventory item (`is_active=True`, `deactivated_at=None`)."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    UPDATE inventory
                    SET is_active = TRUE,
                        deactivated_at = NULL,
                        updated_at = NOW()
                    WHERE id = :item_id
                      AND tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    RETURNING
                        id,
                        tenant_id,
                        company_id,
                        sku,
                        name,
                        description,
                        category,
                        price_vnd,
                        stock_quantity,
                        (embedding IS NOT NULL) AS has_embedding,
                        is_active,
                        deactivated_at,
                        created_at,
                        updated_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {"item_id": item_id, "tenant_id": norm_tenant, "company_id": company_id},
                )
                row = result.mappings().first()
                return InventoryResponse.model_validate(dict(row)) if row else None

        raw = self._inventory.get(item_id)
        if (
            raw is None
            or _normalize_tenant_id(str(raw["tenant_id"])) != norm_tenant
            or (company_id is not None and raw.get("company_id") != company_id)
        ):
            return None
        raw["is_active"] = True
        raw["deactivated_at"] = None
        raw["updated_at"] = datetime.now(UTC)
        return self._inventory_response_from_raw(raw)

    async def soft_delete_customer(
        self,
        customer_id: UUID,
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> CustomerResponse | None:
        """Soft-delete a customer (`is_active=False`, `deactivated_at=NOW()`)."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    UPDATE customers
                    SET is_active = FALSE,
                        deactivated_at = COALESCE(deactivated_at, NOW())
                    WHERE id = :customer_id
                      AND tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    RETURNING id, tenant_id, company_id, meta_psid, full_name, phone, is_active, deactivated_at, created_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {"customer_id": customer_id, "tenant_id": norm_tenant, "company_id": company_id},
                )
                row = result.mappings().first()
                return CustomerResponse.model_validate(dict(row)) if row else None

        existing = self._customers.get(customer_id)
        if (
            existing is None
            or _normalize_tenant_id(existing.tenant_id) != norm_tenant
            or (company_id is not None and existing.company_id != company_id)
        ):
            return None
        now = datetime.now(UTC)
        updated = existing.model_copy(
            update={
                "is_active": False,
                "deactivated_at": existing.deactivated_at or now,
            }
        )
        self._customers[customer_id] = updated
        return updated

    async def restore_customer(
        self,
        customer_id: UUID,
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> CustomerResponse | None:
        """Restore a soft-deleted customer (`is_active=True`, `deactivated_at=None`)."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    UPDATE customers
                    SET is_active = TRUE,
                        deactivated_at = NULL
                    WHERE id = :customer_id
                      AND tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    RETURNING id, tenant_id, company_id, meta_psid, full_name, phone, is_active, deactivated_at, created_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {"customer_id": customer_id, "tenant_id": norm_tenant, "company_id": company_id},
                )
                row = result.mappings().first()
                return CustomerResponse.model_validate(dict(row)) if row else None

        existing = self._customers.get(customer_id)
        if (
            existing is None
            or _normalize_tenant_id(existing.tenant_id) != norm_tenant
            or (company_id is not None and existing.company_id != company_id)
        ):
            return None
        updated = existing.model_copy(update={"is_active": True, "deactivated_at": None})
        self._customers[customer_id] = updated
        return updated

    async def soft_delete_employee(
        self,
        employee_id: UUID,
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> EmployeeResponse | None:
        """Soft-delete an employee (`is_active=False`, `deactivated_at=NOW()`)."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    UPDATE employees
                    SET is_active = FALSE,
                        deactivated_at = COALESCE(deactivated_at, NOW())
                    WHERE id = :employee_id
                      AND tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    RETURNING id, tenant_id, company_id, full_name, role, telegram_user_id, is_active, deactivated_at, created_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {"employee_id": employee_id, "tenant_id": norm_tenant, "company_id": company_id},
                )
                row = result.mappings().first()
                return EmployeeResponse.model_validate(dict(row)) if row else None

        existing = self._employees.get(employee_id)
        if (
            existing is None
            or _normalize_tenant_id(existing.tenant_id) != norm_tenant
            or (company_id is not None and existing.company_id != company_id)
        ):
            return None
        now = datetime.now(UTC)
        updated = existing.model_copy(
            update={
                "is_active": False,
                "deactivated_at": existing.deactivated_at or now,
            }
        )
        self._employees[employee_id] = updated
        return updated

    async def restore_employee(
        self,
        employee_id: UUID,
        tenant_id: str | None = None,
        company_id: UUID | None = None,
    ) -> EmployeeResponse | None:
        """Restore a soft-deleted employee (`is_active=True`, `deactivated_at=None`)."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    UPDATE employees
                    SET is_active = TRUE,
                        deactivated_at = NULL
                    WHERE id = :employee_id
                      AND tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    RETURNING id, tenant_id, company_id, full_name, role, telegram_user_id, is_active, deactivated_at, created_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {"employee_id": employee_id, "tenant_id": norm_tenant, "company_id": company_id},
                )
                row = result.mappings().first()
                return EmployeeResponse.model_validate(dict(row)) if row else None

        existing = self._employees.get(employee_id)
        if (
            existing is None
            or _normalize_tenant_id(existing.tenant_id) != norm_tenant
            or (company_id is not None and existing.company_id != company_id)
        ):
            return None
        updated = existing.model_copy(update={"is_active": True, "deactivated_at": None})
        self._employees[employee_id] = updated
        return updated

    async def collect_expired_inactive_records(
        self,
        tenant_id: str | None = None,
        *,
        cutoff_at: datetime,
        limit: int = 500,
    ) -> dict[str, list[dict[str, Any]]]:
        """Collect expired soft-deleted inventory, customer, and employee records past `cutoff_at`."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        effective_cutoff = cutoff_at if cutoff_at.tzinfo is not None else cutoff_at.replace(tzinfo=UTC)
        effective_limit = max(1, min(int(limit), 5000))

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                inv_res = await session.execute(
                    text(
                        """
                        SELECT id, tenant_id, sku, name, category, price_vnd, stock_quantity, deactivated_at, created_at
                        FROM inventory
                        WHERE tenant_id = :tenant_id
                          AND is_active = FALSE
                          AND deactivated_at IS NOT NULL
                          AND deactivated_at <= :cutoff_at
                        ORDER BY deactivated_at ASC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": norm_tenant, "cutoff_at": effective_cutoff, "limit": effective_limit},
                )
                cust_res = await session.execute(
                    text(
                        """
                        SELECT id, tenant_id, meta_psid, full_name, phone, deactivated_at, created_at
                        FROM customers
                        WHERE tenant_id = :tenant_id
                          AND is_active = FALSE
                          AND deactivated_at IS NOT NULL
                          AND deactivated_at <= :cutoff_at
                        ORDER BY deactivated_at ASC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": norm_tenant, "cutoff_at": effective_cutoff, "limit": effective_limit},
                )
                emp_res = await session.execute(
                    text(
                        """
                        SELECT id, tenant_id, full_name, role, telegram_user_id, deactivated_at, created_at
                        FROM employees
                        WHERE tenant_id = :tenant_id
                          AND is_active = FALSE
                          AND deactivated_at IS NOT NULL
                          AND deactivated_at <= :cutoff_at
                        ORDER BY deactivated_at ASC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": norm_tenant, "cutoff_at": effective_cutoff, "limit": effective_limit},
                )
                return {
                    "inventory": [dict(r) for r in inv_res.mappings().all()],
                    "customers": [dict(r) for r in cust_res.mappings().all()],
                    "employees": [dict(r) for r in emp_res.mappings().all()],
                }

        def _is_expired(is_active: bool, deactivated_at: datetime | None) -> bool:
            if is_active or deactivated_at is None:
                return False
            dt = deactivated_at if deactivated_at.tzinfo is not None else deactivated_at.replace(tzinfo=UTC)
            return dt <= effective_cutoff

        inv_expired = [
            {
                "id": raw["id"],
                "tenant_id": raw["tenant_id"],
                "sku": raw["sku"],
                "name": raw["name"],
                "category": raw.get("category", "Hardware"),
                "price_vnd": raw["price_vnd"],
                "stock_quantity": raw["stock_quantity"],
                "deactivated_at": raw.get("deactivated_at"),
                "created_at": raw["created_at"],
            }
            for raw in self._inventory.values()
            if _normalize_tenant_id(str(raw["tenant_id"])) == norm_tenant
            and _is_expired(bool(raw.get("is_active", True)), raw.get("deactivated_at"))
        ]
        inv_expired.sort(key=lambda item: item["deactivated_at"])

        cust_expired = [
            {
                "id": c.id,
                "tenant_id": c.tenant_id,
                "meta_psid": c.meta_psid,
                "full_name": c.full_name,
                "phone": c.phone,
                "deactivated_at": c.deactivated_at,
                "created_at": c.created_at,
            }
            for c in self._customers.values()
            if _normalize_tenant_id(c.tenant_id) == norm_tenant
            and _is_expired(c.is_active, c.deactivated_at)
        ]
        cust_expired.sort(key=lambda item: item["deactivated_at"])

        emp_expired = [
            {
                "id": e.id,
                "tenant_id": e.tenant_id,
                "full_name": e.full_name,
                "role": e.role,
                "telegram_user_id": e.telegram_user_id,
                "deactivated_at": e.deactivated_at,
                "created_at": e.created_at,
            }
            for e in self._employees.values()
            if _normalize_tenant_id(e.tenant_id) == norm_tenant
            and _is_expired(e.is_active, e.deactivated_at)
        ]
        emp_expired.sort(key=lambda item: item["deactivated_at"])

        return {
            "inventory": inv_expired[:effective_limit],
            "customers": cust_expired[:effective_limit],
            "employees": emp_expired[:effective_limit],
        }

    async def purge_expired_inactive_records(
        self,
        tenant_id: str | None = None,
        *,
        cutoff_at: datetime,
        limit: int = 500,
        dry_run: bool = False,
    ) -> dict[str, dict[str, int]]:
        """Purge expired inactive records past `cutoff_at` while respecting FK constraints."""
        norm_tenant = _resolve_repo_tenant(tenant_id)
        effective_cutoff = cutoff_at if cutoff_at.tzinfo is not None else cutoff_at.replace(tzinfo=UTC)
        effective_limit = max(1, min(int(limit), 5000))

        deleted_counts: dict[str, int] = {"inventory": 0, "customers": 0, "employees": 0}
        skipped_fk: dict[str, int] = {"inventory": 0, "customers": 0, "employees": 0}

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                cust_fk_res = await session.execute(
                    text(
                        """
                        SELECT
                            c.id,
                            EXISTS (
                                SELECT 1
                                FROM orders o
                                WHERE o.tenant_id = :tenant_id
                                  AND o.customer_id = c.id
                            ) AS has_orders
                        FROM customers c
                        WHERE c.tenant_id = :tenant_id
                          AND c.is_active = FALSE
                          AND c.deactivated_at IS NOT NULL
                          AND c.deactivated_at <= :cutoff_at
                        ORDER BY c.deactivated_at ASC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": norm_tenant, "cutoff_at": effective_cutoff, "limit": effective_limit},
                )
                cust_rows = cust_fk_res.mappings().all()
                protected_cust_ids = [r["id"] for r in cust_rows if bool(r["has_orders"])]
                deletable_cust_ids = [r["id"] for r in cust_rows if not bool(r["has_orders"])]
                skipped_fk["customers"] = len(protected_cust_ids)

                if not dry_run:
                    inv_del = await session.execute(
                        text(
                            """
                            WITH target AS (
                                SELECT id
                                FROM inventory
                                WHERE tenant_id = :tenant_id
                                  AND is_active = FALSE
                                  AND deactivated_at IS NOT NULL
                                  AND deactivated_at <= :cutoff_at
                                ORDER BY deactivated_at ASC
                                LIMIT :limit
                            )
                            DELETE FROM inventory
                            WHERE tenant_id = :tenant_id
                              AND id IN (SELECT id FROM target)
                            RETURNING id
                            """
                        ),
                        {
                            "tenant_id": norm_tenant,
                            "cutoff_at": effective_cutoff,
                            "limit": effective_limit,
                        },
                    )
                    deleted_counts["inventory"] = len(inv_del.fetchall())

                    if deletable_cust_ids:
                        cust_del = await session.execute(
                            text(
                                """
                                DELETE FROM customers
                                WHERE tenant_id = :tenant_id
                                  AND id = ANY(:ids)
                                  AND NOT EXISTS (
                                      SELECT 1
                                      FROM orders o
                                      WHERE o.tenant_id = :tenant_id
                                        AND o.customer_id = customers.id
                                  )
                                RETURNING id
                                """
                            ),
                            {"tenant_id": norm_tenant, "ids": deletable_cust_ids},
                        )
                        deleted_counts["customers"] = len(cust_del.fetchall())

                    emp_del = await session.execute(
                        text(
                            """
                            WITH target AS (
                                SELECT id
                                FROM employees
                                WHERE tenant_id = :tenant_id
                                  AND is_active = FALSE
                                  AND deactivated_at IS NOT NULL
                                  AND deactivated_at <= :cutoff_at
                                ORDER BY deactivated_at ASC
                                LIMIT :limit
                            )
                            DELETE FROM employees
                            WHERE tenant_id = :tenant_id
                              AND id IN (SELECT id FROM target)
                            RETURNING id
                            """
                        ),
                        {
                            "tenant_id": norm_tenant,
                            "cutoff_at": effective_cutoff,
                            "limit": effective_limit,
                        },
                    )
                    deleted_counts["employees"] = len(emp_del.fetchall())

            return {
                "deleted_counts": deleted_counts,
                "skipped_fk_protected_counts": skipped_fk,
            }

        expired = await self.collect_expired_inactive_records(
            tenant_id=norm_tenant,
            cutoff_at=effective_cutoff,
            limit=effective_limit,
        )
        referenced_customer_ids = {
            order.customer_id
            for order in self._orders.values()
            if _normalize_tenant_id(order.tenant_id) == norm_tenant
        }

        deletable_customer_ids: list[UUID] = []
        for row in expired["customers"]:
            cid = UUID(str(row["id"]))
            if cid in referenced_customer_ids:
                skipped_fk["customers"] += 1
            else:
                deletable_customer_ids.append(cid)

        if dry_run:
            return {
                "deleted_counts": deleted_counts,
                "skipped_fk_protected_counts": skipped_fk,
            }

        for row in expired["inventory"]:
            iid = UUID(str(row["id"]))
            if self._inventory.pop(iid, None) is not None:
                deleted_counts["inventory"] += 1

        for cid in deletable_customer_ids:
            if self._customers.pop(cid, None) is not None:
                deleted_counts["customers"] += 1

        for row in expired["employees"]:
            eid = UUID(str(row["id"]))
            if self._employees.pop(eid, None) is not None:
                deleted_counts["employees"] += 1
                # Mirror PostgreSQL `ON DELETE SET NULL` on orders.assigned_employee_id
                for oid, order in list(self._orders.items()):
                    if (
                        _normalize_tenant_id(order.tenant_id) == norm_tenant
                        and order.assigned_employee_id == eid
                    ):
                        self._orders[oid] = order.model_copy(update={"assigned_employee_id": None})

        return {
            "deleted_counts": deleted_counts,
            "skipped_fk_protected_counts": skipped_fk,
        }

    # --------------------------------------------------------------------------
    # Multi-Company, Manager/Staff Provisioning & Omnichannel Chat Parity
    # --------------------------------------------------------------------------

    async def create_company(
        self,
        *,
        tenant_id: str,
        name: str,
        code: str,
        bank_code: str,
        bank_name: str,
        bank_account_number: str,
        bank_account_name: str,
        sepay_sub_account: str = "",
        sepay_memo_prefix: str = "",
        sepay_bank_account_id: str | None = None,
        meta_page_id: str | None = None,
    ) -> CompanyResponse:
        """Create a tenant-scoped company and register its SePay/Meta channel routing."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_code = code.strip().upper()
        norm_sub = (sepay_sub_account or "").strip()
        norm_acc = bank_account_number.strip()

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    INSERT INTO companies (
                        tenant_id, name, code, bank_code, bank_name,
                        bank_account_number, bank_account_name,
                        sepay_sub_account, sepay_memo_prefix, sepay_bank_account_id
                    )
                    VALUES (
                        :tenant_id, :name, :code, :bank_code, :bank_name,
                        :bank_account_number, :bank_account_name,
                        :sepay_sub_account, :sepay_memo_prefix, :sepay_bank_account_id
                    )
                    RETURNING
                        id, tenant_id, name, code, bank_code, bank_name,
                        bank_account_number, bank_account_name,
                        sepay_sub_account, sepay_memo_prefix, sepay_bank_account_id,
                        sepay_bankhub_company_xid, is_active, deactivated_at,
                        created_at, updated_at
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "name": name.strip(),
                        "code": norm_code,
                        "bank_code": bank_code.strip().upper(),
                        "bank_name": bank_name.strip(),
                        "bank_account_number": norm_acc,
                        "bank_account_name": bank_account_name.strip(),
                        "sepay_sub_account": norm_sub,
                        "sepay_memo_prefix": sepay_memo_prefix.strip(),
                        "sepay_bank_account_id": (
                            sepay_bank_account_id.strip() if sepay_bank_account_id else None
                        ),
                    },
                )
                company = CompanyResponse.model_validate(dict(res.mappings().one()))
                await self.register_company_channel(
                    tenant_id=norm_tenant,
                    company_id=company.id,
                    channel_type="sepay_account",
                    external_id=norm_acc,
                    sub_account=norm_sub,
                    session=session,
                )
                if meta_page_id and meta_page_id.strip():
                    await self.register_company_channel(
                        tenant_id=norm_tenant,
                        company_id=company.id,
                        channel_type="meta_page",
                        external_id=meta_page_id.strip(),
                        sub_account="",
                        session=session,
                    )
                return company

        for existing in self._companies.values():
            if (
                _normalize_tenant_id(existing.tenant_id) == norm_tenant
                and existing.code.upper() == norm_code
            ):
                raise ValueError(f"Company code '{norm_code}' already exists for tenant")

        now = datetime.now(UTC)
        company = CompanyResponse(
            id=uuid4(),
            tenant_id=norm_tenant,
            name=name.strip(),
            code=norm_code,
            bank_code=bank_code.strip().upper(),
            bank_name=bank_name.strip(),
            bank_account_number=norm_acc,
            bank_account_name=bank_account_name.strip(),
            sepay_sub_account=norm_sub,
            sepay_memo_prefix=sepay_memo_prefix.strip(),
            sepay_bank_account_id=(
                sepay_bank_account_id.strip() if sepay_bank_account_id else None
            ),
            sepay_bankhub_company_xid=None,
            is_active=True,
            deactivated_at=None,
            created_at=now,
            updated_at=now,
        )
        channels_backup = dict(self._tenant_channels)
        self._companies[company.id] = company
        try:
            await self.register_company_channel(
                tenant_id=norm_tenant,
                company_id=company.id,
                channel_type="sepay_account",
                external_id=norm_acc,
                sub_account=norm_sub,
            )
            if meta_page_id and meta_page_id.strip():
                await self.register_company_channel(
                    tenant_id=norm_tenant,
                    company_id=company.id,
                    channel_type="meta_page",
                    external_id=meta_page_id.strip(),
                    sub_account="",
                )
        except Exception:
            self._companies.pop(company.id, None)
            self._tenant_channels.clear()
            self._tenant_channels.update(channels_backup)
            raise
        return company

    async def list_companies(
        self,
        tenant_id: str,
        *,
        include_inactive: bool = False,
        company_id: UUID | None = None,
    ) -> list[CompanyResponse]:
        """List companies scoped to `tenant_id` (and optionally a specific `company_id`)."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        id, tenant_id, name, code, bank_code, bank_name,
                        bank_account_number, bank_account_name,
                        sepay_sub_account, sepay_memo_prefix, sepay_bank_account_id,
                        sepay_bankhub_company_xid, is_active, deactivated_at,
                        created_at, updated_at
                    FROM companies
                    WHERE tenant_id = :tenant_id
                      AND (:include_inactive OR is_active = TRUE)
                      AND (CAST(:company_id AS uuid) IS NULL OR id = CAST(:company_id AS uuid))
                    ORDER BY created_at ASC, name ASC
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "include_inactive": include_inactive,
                        "company_id": company_id,
                    },
                )
                return [CompanyResponse.model_validate(dict(r)) for r in res.mappings().all()]

        return [
            comp
            for comp in self._companies.values()
            if _normalize_tenant_id(comp.tenant_id) == norm_tenant
            and (include_inactive or comp.is_active)
            and (company_id is None or comp.id == company_id)
        ]

    async def get_company(
        self,
        company_id: UUID,
        tenant_id: str,
        *,
        include_inactive: bool = False,
    ) -> CompanyResponse | None:
        """Fetch a single tenant-scoped company by ID."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        id, tenant_id, name, code, bank_code, bank_name,
                        bank_account_number, bank_account_name,
                        sepay_sub_account, sepay_memo_prefix, sepay_bank_account_id,
                        sepay_bankhub_company_xid, is_active, deactivated_at,
                        created_at, updated_at
                    FROM companies
                    WHERE id = :company_id
                      AND tenant_id = :tenant_id
                      AND (:include_inactive OR is_active = TRUE)
                    LIMIT 1
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "company_id": company_id,
                        "tenant_id": norm_tenant,
                        "include_inactive": include_inactive,
                    },
                )
                row = res.mappings().first()
                return CompanyResponse.model_validate(dict(row)) if row else None

        comp = self._companies.get(company_id)
        if comp is None or _normalize_tenant_id(comp.tenant_id) != norm_tenant:
            return None
        if not include_inactive and not comp.is_active:
            return None
        return comp

    async def update_company_bank(
        self,
        *,
        company_id: UUID,
        tenant_id: str,
        bank_code: str,
        bank_name: str,
        bank_account_number: str,
        bank_account_name: str,
        sepay_sub_account: str = "",
        sepay_memo_prefix: str = "",
        sepay_bank_account_id: str | None = None,
    ) -> CompanyResponse | None:
        """Update a company's bank & SePay settings and re-sync `tenant_channels`."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_acc = bank_account_number.strip()
        norm_sub = (sepay_sub_account or "").strip()

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE companies
                    SET bank_code = :bank_code,
                        bank_name = :bank_name,
                        bank_account_number = :bank_account_number,
                        bank_account_name = :bank_account_name,
                        sepay_sub_account = :sepay_sub_account,
                        sepay_memo_prefix = :sepay_memo_prefix,
                        sepay_bank_account_id = :sepay_bank_account_id,
                        updated_at = NOW()
                    WHERE id = :company_id
                      AND tenant_id = :tenant_id
                      AND is_active = TRUE
                    RETURNING
                        id, tenant_id, name, code, bank_code, bank_name,
                        bank_account_number, bank_account_name,
                        sepay_sub_account, sepay_memo_prefix, sepay_bank_account_id,
                        sepay_bankhub_company_xid, is_active, deactivated_at,
                        created_at, updated_at
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "company_id": company_id,
                        "tenant_id": norm_tenant,
                        "bank_code": bank_code.strip().upper(),
                        "bank_name": bank_name.strip(),
                        "bank_account_number": norm_acc,
                        "bank_account_name": bank_account_name.strip(),
                        "sepay_sub_account": norm_sub,
                        "sepay_memo_prefix": sepay_memo_prefix.strip(),
                        "sepay_bank_account_id": (
                            sepay_bank_account_id.strip() if sepay_bank_account_id else None
                        ),
                    },
                )
                row = res.mappings().first()
                if row is None:
                    return None
                updated = CompanyResponse.model_validate(dict(row))
                await self.register_company_channel(
                    tenant_id=norm_tenant,
                    company_id=company_id,
                    channel_type="sepay_account",
                    external_id=norm_acc,
                    sub_account=norm_sub,
                    session=session,
                )
                return updated

        existing = self._companies.get(company_id)
        if (
            existing is None
            or _normalize_tenant_id(existing.tenant_id) != norm_tenant
            or not existing.is_active
        ):
            return None
        updated = existing.model_copy(
            update={
                "bank_code": bank_code.strip().upper(),
                "bank_name": bank_name.strip(),
                "bank_account_number": norm_acc,
                "bank_account_name": bank_account_name.strip(),
                "sepay_sub_account": norm_sub,
                "sepay_memo_prefix": sepay_memo_prefix.strip(),
                "sepay_bank_account_id": (
                    sepay_bank_account_id.strip() if sepay_bank_account_id else None
                ),
                "updated_at": datetime.now(UTC),
            }
        )
        self._companies[company_id] = updated
        try:
            await self.register_company_channel(
                tenant_id=norm_tenant,
                company_id=company_id,
                channel_type="sepay_account",
                external_id=norm_acc,
                sub_account=norm_sub,
            )
        except Exception:
            self._companies[company_id] = existing
            raise
        return updated

    async def update_company_bankhub_xid(
        self,
        *,
        company_id: UUID,
        tenant_id: str,
        xid: str,
    ) -> CompanyResponse | None:
        """Persist `sepay_bankhub_company_xid` for an active tenant company."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE companies
                    SET sepay_bankhub_company_xid = :xid,
                        updated_at = NOW()
                    WHERE id = :company_id
                      AND tenant_id = :tenant_id
                    RETURNING
                        id, tenant_id, name, code, bank_code, bank_name,
                        bank_account_number, bank_account_name,
                        sepay_sub_account, sepay_memo_prefix, sepay_bank_account_id,
                        sepay_bankhub_company_xid, is_active, deactivated_at,
                        created_at, updated_at
                    """
                )
                res = await session.execute(
                    stmt,
                    {"company_id": company_id, "tenant_id": norm_tenant, "xid": xid.strip()},
                )
                row = res.mappings().first()
                return CompanyResponse.model_validate(dict(row)) if row else None

        existing = self._companies.get(company_id)
        if existing is None or _normalize_tenant_id(existing.tenant_id) != norm_tenant:
            return None
        updated = existing.model_copy(
            update={"sepay_bankhub_company_xid": xid.strip(), "updated_at": datetime.now(UTC)}
        )
        self._companies[company_id] = updated
        return updated

    async def soft_delete_company(
        self,
        company_id: UUID,
        tenant_id: str,
    ) -> CompanyResponse | None:
        """Soft-delete a company (`is_active=False`, `deactivated_at=NOW()`)."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE companies
                    SET is_active = FALSE,
                        deactivated_at = COALESCE(deactivated_at, NOW()),
                        updated_at = NOW()
                    WHERE id = :company_id
                      AND tenant_id = :tenant_id
                    RETURNING
                        id, tenant_id, name, code, bank_code, bank_name,
                        bank_account_number, bank_account_name,
                        sepay_sub_account, sepay_memo_prefix, sepay_bank_account_id,
                        sepay_bankhub_company_xid, is_active, deactivated_at,
                        created_at, updated_at
                    """
                )
                res = await session.execute(
                    stmt,
                    {"company_id": company_id, "tenant_id": norm_tenant},
                )
                row = res.mappings().first()
                return CompanyResponse.model_validate(dict(row)) if row else None

        existing = self._companies.get(company_id)
        if existing is None or _normalize_tenant_id(existing.tenant_id) != norm_tenant:
            return None
        now = datetime.now(UTC)
        updated = existing.model_copy(
            update={
                "is_active": False,
                "deactivated_at": existing.deactivated_at or now,
                "updated_at": now,
            }
        )
        self._companies[company_id] = updated
        return updated

    async def restore_company(
        self,
        company_id: UUID,
        tenant_id: str,
    ) -> CompanyResponse | None:
        """Restore a soft-deleted company (`is_active=True`, `deactivated_at=None`)."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE companies
                    SET is_active = TRUE,
                        deactivated_at = NULL,
                        updated_at = NOW()
                    WHERE id = :company_id
                      AND tenant_id = :tenant_id
                    RETURNING
                        id, tenant_id, name, code, bank_code, bank_name,
                        bank_account_number, bank_account_name,
                        sepay_sub_account, sepay_memo_prefix, sepay_bank_account_id,
                        sepay_bankhub_company_xid, is_active, deactivated_at,
                        created_at, updated_at
                    """
                )
                res = await session.execute(
                    stmt,
                    {"company_id": company_id, "tenant_id": norm_tenant},
                )
                row = res.mappings().first()
                return CompanyResponse.model_validate(dict(row)) if row else None

        existing = self._companies.get(company_id)
        if existing is None or _normalize_tenant_id(existing.tenant_id) != norm_tenant:
            return None
        updated = existing.model_copy(
            update={"is_active": True, "deactivated_at": None, "updated_at": datetime.now(UTC)}
        )
        self._companies[company_id] = updated
        return updated

    async def list_company_managers(
        self,
        *,
        tenant_id: str,
        company_id: UUID,
    ) -> list[dict[str, Any]]:
        """List manager user accounts bound to a specific company."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        u.id AS user_id,
                        u.tenant_id,
                        u.company_id,
                        COALESCE(c.name, '') AS company_name,
                        u.email,
                        u.username,
                        u.full_name,
                        u.must_change_password,
                        u.is_active,
                        u.created_at
                    FROM users u
                    LEFT JOIN companies c ON c.id = u.company_id AND c.tenant_id = u.tenant_id
                    WHERE u.tenant_id = :tenant_id
                      AND u.company_id = :company_id
                      AND u.role = 'manager'
                    ORDER BY u.created_at ASC
                    """
                )
                res = await session.execute(
                    stmt,
                    {"tenant_id": norm_tenant, "company_id": company_id},
                )
                return [dict(r) for r in res.mappings().all()]

        comp = self._companies.get(company_id)
        comp_name = comp.name if comp else ""
        results: list[dict[str, Any]] = []
        for user in self._users.values():
            if (
                _normalize_tenant_id(str(user["tenant_id"])) == norm_tenant
                and user.get("company_id") == company_id
                and str(user.get("role", "")).lower() == "manager"
            ):
                results.append(
                    {
                        "user_id": user["id"],
                        "tenant_id": norm_tenant,
                        "company_id": company_id,
                        "company_name": comp_name,
                        "email": user["email"],
                        "username": user.get("username"),
                        "full_name": user.get("full_name", ""),
                        "must_change_password": bool(user.get("must_change_password", False)),
                        "is_active": bool(user.get("is_active", True)),
                        "created_at": user["created_at"],
                    }
                )
        return results

    async def create_staff_member(
        self,
        *,
        tenant_id: str,
        company_id: UUID,
        full_name: str,
        role: str,
        telegram_user_id: str,
        email: str,
        username: str,
        password_hash: str,
    ) -> dict[str, Any]:
        """Atomically create an `Employee` and linked `UserAccount(role='staff', must_change_password=True)`."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_email = email.strip().lower()
        norm_username = username.strip()
        clean_name = full_name.strip()
        clean_emp_role = role.strip() or "fulfillment_specialist"
        clean_tg = telegram_user_id.strip()

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                emp_stmt = text(
                    """
                    INSERT INTO employees (
                        tenant_id, company_id, full_name, role, telegram_user_id
                    )
                    VALUES (
                        :tenant_id, :company_id, :full_name, :role, :telegram_user_id
                    )
                    RETURNING id, created_at
                    """
                )
                emp_res = await session.execute(
                    emp_stmt,
                    {
                        "tenant_id": norm_tenant,
                        "company_id": company_id,
                        "full_name": clean_name,
                        "role": clean_emp_role,
                        "telegram_user_id": clean_tg,
                    },
                )
                emp_row = emp_res.mappings().one()
                emp_id = UUID(str(emp_row["id"]))

                user_stmt = text(
                    """
                    INSERT INTO users (
                        tenant_id, email, username, full_name, password_hash, role,
                        company_id, employee_id, must_change_password, is_active
                    )
                    VALUES (
                        :tenant_id, :email, :username, :full_name, :password_hash, 'staff',
                        :company_id, :employee_id, TRUE, TRUE
                    )
                    RETURNING id, created_at
                    """
                )
                user_res = await session.execute(
                    user_stmt,
                    {
                        "tenant_id": norm_tenant,
                        "email": norm_email,
                        "username": norm_username,
                        "full_name": clean_name,
                        "password_hash": password_hash,
                        "company_id": company_id,
                        "employee_id": emp_id,
                    },
                )
                user_row = user_res.mappings().one()
                return {
                    "employee_id": emp_id,
                    "user_id": UUID(str(user_row["id"])),
                    "tenant_id": norm_tenant,
                    "company_id": company_id,
                    "full_name": clean_name,
                    "role": clean_emp_role,
                    "telegram_user_id": clean_tg,
                    "email": norm_email,
                    "username": norm_username,
                    "must_change_password": True,
                    "created_at": user_row["created_at"],
                }

        for existing in self._users.values():
            if _normalize_tenant_id(str(existing["tenant_id"])) != norm_tenant:
                continue
            if str(existing["email"]).lower() == norm_email:
                raise ValueError(f"User {norm_email} already exists for tenant {norm_tenant}")
            if existing.get("username") and str(existing["username"]).lower() == norm_username.lower():
                raise ValueError(f"Username {norm_username} already exists for tenant {norm_tenant}")

        now = datetime.now(UTC)
        emp_id = uuid4()
        user_id = uuid4()
        emp = EmployeeResponse(
            id=emp_id,
            tenant_id=norm_tenant,
            company_id=company_id,
            full_name=clean_name,
            role=clean_emp_role,
            telegram_user_id=clean_tg,
            is_active=True,
            deactivated_at=None,
            created_at=now,
        )
        self._employees[emp_id] = emp
        self._users[user_id] = {
            "id": user_id,
            "tenant_id": norm_tenant,
            "email": norm_email,
            "username": norm_username,
            "full_name": clean_name,
            "password_hash": password_hash,
            "role": "staff",
            "company_id": company_id,
            "employee_id": emp_id,
            "must_change_password": True,
            "password_changed_at": None,
            "is_active": True,
            "created_at": now,
        }
        return {
            "employee_id": emp_id,
            "user_id": user_id,
            "tenant_id": norm_tenant,
            "company_id": company_id,
            "full_name": clean_name,
            "role": clean_emp_role,
            "telegram_user_id": clean_tg,
            "email": norm_email,
            "username": norm_username,
            "must_change_password": True,
            "created_at": now,
        }

    async def list_staff_members(
        self,
        *,
        tenant_id: str,
        company_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """List staff employees with linked user credentials and active/completed order counts."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    SELECT
                        e.id AS employee_id,
                        u.id AS user_id,
                        e.tenant_id,
                        e.company_id,
                        e.full_name,
                        e.role,
                        e.telegram_user_id,
                        u.email,
                        u.username,
                        COALESCE(u.must_change_password, FALSE) AS must_change_password,
                        e.is_active,
                        COUNT(o.id) FILTER (
                            WHERE o.is_active = TRUE
                              AND o.status IN ('PENDING_PAYMENT', 'PAID', 'FULFILLING')
                        ) AS active_orders_count,
                        COUNT(o.id) FILTER (
                            WHERE o.is_active = TRUE
                              AND o.status = 'COMPLETED'
                        ) AS completed_orders_count,
                        e.created_at
                    FROM employees e
                    LEFT JOIN users u
                        ON u.employee_id = e.id AND u.tenant_id = e.tenant_id
                    LEFT JOIN orders o
                        ON o.assigned_employee_id = e.id AND o.tenant_id = e.tenant_id
                    WHERE e.tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR e.company_id = CAST(:company_id AS uuid))
                      AND (:include_inactive OR e.is_active = TRUE)
                    GROUP BY
                        e.id, u.id, e.tenant_id, e.company_id, e.full_name, e.role,
                        e.telegram_user_id, u.email, u.username, u.must_change_password,
                        e.is_active, e.created_at
                    ORDER BY e.created_at ASC
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "company_id": company_id,
                        "include_inactive": include_inactive,
                    },
                )
                return [dict(r) for r in res.mappings().all()]

        users_by_emp: dict[UUID, dict[str, Any]] = {}
        for user in self._users.values():
            if (
                _normalize_tenant_id(str(user["tenant_id"])) == norm_tenant
                and user.get("employee_id") is not None
            ):
                users_by_emp[UUID(str(user["employee_id"]))] = user

        results: list[dict[str, Any]] = []
        for emp in self._employees.values():
            if _normalize_tenant_id(emp.tenant_id) != norm_tenant:
                continue
            if company_id is not None and emp.company_id != company_id:
                continue
            if not include_inactive and not emp.is_active:
                continue
            linked = users_by_emp.get(emp.id)
            active_cnt = sum(
                1
                for o in self._orders.values()
                if _normalize_tenant_id(o.tenant_id) == norm_tenant
                and o.is_active
                and o.assigned_employee_id == emp.id
                and o.status in (OrderStatus.PENDING_PAYMENT, OrderStatus.PAID, OrderStatus.FULFILLING)
            )
            completed_cnt = sum(
                1
                for o in self._orders.values()
                if _normalize_tenant_id(o.tenant_id) == norm_tenant
                and o.is_active
                and o.assigned_employee_id == emp.id
                and o.status == OrderStatus.COMPLETED
            )
            results.append(
                {
                    "employee_id": emp.id,
                    "user_id": linked["id"] if linked else None,
                    "tenant_id": norm_tenant,
                    "company_id": emp.company_id,
                    "full_name": emp.full_name,
                    "role": emp.role,
                    "telegram_user_id": emp.telegram_user_id,
                    "email": linked.get("email") if linked else None,
                    "username": linked.get("username") if linked else None,
                    "must_change_password": bool(linked.get("must_change_password", False)) if linked else False,
                    "is_active": emp.is_active,
                    "active_orders_count": active_cnt,
                    "completed_orders_count": completed_cnt,
                    "created_at": emp.created_at,
                }
            )
        return results

    async def upsert_channel_conversation(
        self,
        *,
        tenant_id: str,
        company_id: UUID | None,
        customer_id: UUID | None,
        channel: str,
        external_thread_id: str,
        customer_name: str,
        last_message_preview: str,
        increment_unread: int = 0,
        reset_unread: bool = False,
        last_message_at: datetime | None = None,
    ) -> ChannelConversationResponse:
        """Create or update an omnichannel conversation thread."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_channel = channel.strip().lower()
        if norm_channel not in ("messenger", "instagram", "telegram", "web", "other"):
            norm_channel = "other"
        norm_thread = external_thread_id.strip()
        msg_time = last_message_at or datetime.now(UTC)
        preview = (last_message_preview or "").strip()[:300]
        clean_cust_name = (customer_name or "Customer").strip() or "Customer"

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    INSERT INTO channel_conversations (
                        tenant_id, company_id, customer_id, channel, external_thread_id,
                        customer_name, last_message_preview, last_message_at, unread_count, status
                    )
                    VALUES (
                        :tenant_id, :company_id, :customer_id, :channel, :external_thread_id,
                        :customer_name, :preview, :msg_at, :initial_unread, 'open'
                    )
                    ON CONFLICT (
                        tenant_id,
                        COALESCE(company_id, '00000000-0000-0000-0000-000000000000'::uuid),
                        channel,
                        external_thread_id
                    ) DO UPDATE SET
                        customer_id = COALESCE(EXCLUDED.customer_id, channel_conversations.customer_id),
                        customer_name = CASE
                            WHEN EXCLUDED.customer_name <> '' AND EXCLUDED.customer_name <> 'Customer'
                            THEN EXCLUDED.customer_name
                            ELSE channel_conversations.customer_name
                        END,
                        last_message_preview = EXCLUDED.last_message_preview,
                        last_message_at = EXCLUDED.last_message_at,
                        unread_count = CASE
                            WHEN :reset_unread THEN 0
                            WHEN :inc_unread > 0 THEN channel_conversations.unread_count + :inc_unread
                            ELSE channel_conversations.unread_count
                        END,
                        updated_at = NOW()
                    RETURNING
                        id, tenant_id, company_id, customer_id, channel, external_thread_id,
                        customer_name, last_message_preview, last_message_at, unread_count,
                        status, created_at, updated_at
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "company_id": company_id,
                        "customer_id": customer_id,
                        "channel": norm_channel,
                        "external_thread_id": norm_thread,
                        "customer_name": clean_cust_name,
                        "preview": preview,
                        "msg_at": msg_time,
                        "initial_unread": max(0, increment_unread),
                        "inc_unread": increment_unread,
                        "reset_unread": reset_unread,
                    },
                )
                return ChannelConversationResponse.model_validate(dict(res.mappings().one()))

        for cid, existing in self._channel_conversations.items():
            if (
                _normalize_tenant_id(existing.tenant_id) == norm_tenant
                and existing.company_id == company_id
                and existing.channel == norm_channel
                and existing.external_thread_id == norm_thread
            ):
                new_unread = (
                    0
                    if reset_unread
                    else (
                        existing.unread_count + increment_unread
                        if increment_unread > 0
                        else existing.unread_count
                    )
                )
                updated_name = (
                    clean_cust_name
                    if clean_cust_name and clean_cust_name != "Customer"
                    else existing.customer_name
                )
                updated = existing.model_copy(
                    update={
                        "customer_id": customer_id or existing.customer_id,
                        "customer_name": updated_name,
                        "last_message_preview": preview,
                        "last_message_at": msg_time,
                        "unread_count": new_unread,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self._channel_conversations[cid] = updated
                return updated

        now = datetime.now(UTC)
        conv = ChannelConversationResponse(
            id=uuid4(),
            tenant_id=norm_tenant,
            company_id=company_id,
            customer_id=customer_id,
            channel=norm_channel,
            external_thread_id=norm_thread,
            customer_name=clean_cust_name,
            last_message_preview=preview,
            last_message_at=msg_time,
            unread_count=max(0, increment_unread) if not reset_unread else 0,
            status="open",
            created_at=now,
            updated_at=now,
        )
        self._channel_conversations[conv.id] = conv
        return conv

    async def append_channel_message(
        self,
        *,
        tenant_id: str,
        company_id: UUID | None,
        conversation_id: UUID,
        channel: str,
        direction: str,
        sender_type: str,
        sender_name: str,
        content: str,
        external_message_id: str | None = None,
        order_code: str | None = None,
        reset_unread: bool = False,
        increment_unread: int = 0,
    ) -> ChannelMessageResponse:
        """Persist a `channel_messages` row and sync the parent conversation preview."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_channel = channel.strip().lower()
        if norm_channel not in ("messenger", "instagram", "telegram", "web", "other"):
            norm_channel = "other"
        now = datetime.now(UTC)
        preview = content.strip()[:300]

        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                msg_stmt = text(
                    """
                    INSERT INTO channel_messages (
                        tenant_id, company_id, conversation_id, channel,
                        external_message_id, direction, sender_type, sender_name,
                        content, order_code
                    )
                    VALUES (
                        :tenant_id, :company_id, :conversation_id, :channel,
                        :external_message_id, :direction, :sender_type, :sender_name,
                        :content, :order_code
                    )
                    RETURNING
                        id, tenant_id, company_id, conversation_id, channel,
                        external_message_id, direction, sender_type, sender_name,
                        content, order_code, created_at
                    """
                )
                res = await session.execute(
                    msg_stmt,
                    {
                        "tenant_id": norm_tenant,
                        "company_id": company_id,
                        "conversation_id": conversation_id,
                        "channel": norm_channel,
                        "external_message_id": external_message_id,
                        "direction": direction,
                        "sender_type": sender_type,
                        "sender_name": sender_name.strip(),
                        "content": content,
                        "order_code": order_code,
                    },
                )
                msg = ChannelMessageResponse.model_validate(dict(res.mappings().one()))

                conv_update = text(
                    """
                    UPDATE channel_conversations
                    SET last_message_preview = :preview,
                        last_message_at = :msg_at,
                        unread_count = CASE
                            WHEN :reset_unread THEN 0
                            WHEN :inc_unread > 0 THEN unread_count + :inc_unread
                            ELSE unread_count
                        END,
                        updated_at = NOW()
                    WHERE id = :conversation_id
                      AND tenant_id = :tenant_id
                    """
                )
                await session.execute(
                    conv_update,
                    {
                        "preview": preview,
                        "msg_at": msg.created_at,
                        "reset_unread": reset_unread,
                        "inc_unread": increment_unread,
                        "conversation_id": conversation_id,
                        "tenant_id": norm_tenant,
                    },
                )
                return msg

        msg = ChannelMessageResponse(
            id=uuid4(),
            tenant_id=norm_tenant,
            company_id=company_id,
            conversation_id=conversation_id,
            channel=norm_channel,
            external_message_id=external_message_id,
            direction=direction,
            sender_type=sender_type,
            sender_name=sender_name.strip(),
            content=content,
            order_code=order_code,
            created_at=now,
        )
        self._channel_messages[msg.id] = msg
        conv = self._channel_conversations.get(conversation_id)
        if conv is not None and _normalize_tenant_id(conv.tenant_id) == norm_tenant:
            new_unread = (
                0
                if reset_unread
                else (
                    conv.unread_count + increment_unread
                    if increment_unread > 0
                    else conv.unread_count
                )
            )
            self._channel_conversations[conversation_id] = conv.model_copy(
                update={
                    "last_message_preview": preview,
                    "last_message_at": now,
                    "unread_count": new_unread,
                    "updated_at": now,
                }
            )
        return msg

    async def list_channel_conversations(
        self,
        *,
        tenant_id: str,
        company_id: UUID | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[ChannelConversationResponse]:
        """List omnichannel conversations ordered by most recent activity."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        norm_channel = channel.strip().lower() if channel and channel.strip() else None
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    SELECT
                        id, tenant_id, company_id, customer_id, channel, external_thread_id,
                        customer_name, last_message_preview, last_message_at, unread_count,
                        status, created_at, updated_at
                    FROM channel_conversations
                    WHERE tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                      AND (CAST(:channel AS text) IS NULL OR channel = CAST(:channel AS text))
                    ORDER BY last_message_at DESC
                    LIMIT :limit
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "company_id": company_id,
                        "channel": norm_channel,
                        "limit": limit,
                    },
                )
                return [ChannelConversationResponse.model_validate(dict(r)) for r in res.mappings().all()]

        convs = [
            c
            for c in self._channel_conversations.values()
            if _normalize_tenant_id(c.tenant_id) == norm_tenant
            and (company_id is None or c.company_id == company_id)
            and (norm_channel is None or c.channel == norm_channel)
        ]
        convs.sort(key=lambda item: item.last_message_at, reverse=True)
        return convs[:limit]

    async def get_channel_conversation(
        self,
        *,
        conversation_id: UUID,
        tenant_id: str,
        company_id: UUID | None = None,
    ) -> ChannelConversationResponse | None:
        """Fetch a single omnichannel conversation within tenant (and optional company) scope."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    SELECT
                        id, tenant_id, company_id, customer_id, channel, external_thread_id,
                        customer_name, last_message_preview, last_message_at, unread_count,
                        status, created_at, updated_at
                    FROM channel_conversations
                    WHERE id = :conversation_id
                      AND tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    LIMIT 1
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "conversation_id": conversation_id,
                        "tenant_id": norm_tenant,
                        "company_id": company_id,
                    },
                )
                row = res.mappings().first()
                return ChannelConversationResponse.model_validate(dict(row)) if row else None

        conv = self._channel_conversations.get(conversation_id)
        if conv is None or _normalize_tenant_id(conv.tenant_id) != norm_tenant:
            return None
        if company_id is not None and conv.company_id != company_id:
            return None
        return conv

    async def list_channel_messages(
        self,
        *,
        conversation_id: UUID,
        tenant_id: str,
        company_id: UUID | None = None,
        limit: int = 100,
    ) -> list[ChannelMessageResponse]:
        """List messages for a conversation ordered chronologically."""
        norm_tenant = _normalize_tenant_id(tenant_id)
        if await self.is_postgres_active():
            async with tenant_db_session(norm_tenant, company_id=company_id) as session:
                stmt = text(
                    """
                    SELECT
                        id, tenant_id, company_id, conversation_id, channel,
                        external_message_id, direction, sender_type, sender_name,
                        content, order_code, created_at
                    FROM channel_messages
                    WHERE conversation_id = :conversation_id
                      AND tenant_id = :tenant_id
                      AND (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid))
                    ORDER BY created_at ASC
                    LIMIT :limit
                    """
                )
                res = await session.execute(
                    stmt,
                    {
                        "conversation_id": conversation_id,
                        "tenant_id": norm_tenant,
                        "company_id": company_id,
                        "limit": limit,
                    },
                )
                return [ChannelMessageResponse.model_validate(dict(r)) for r in res.mappings().all()]

        msgs = [
            m
            for m in self._channel_messages.values()
            if _normalize_tenant_id(m.tenant_id) == norm_tenant
            and m.conversation_id == conversation_id
            and (company_id is None or m.company_id == company_id)
        ]
        msgs.sort(key=lambda item: item.created_at)
        return msgs[:limit]


InMemoryERPStore = ERPRepository
erp_repository = ERPRepository()
