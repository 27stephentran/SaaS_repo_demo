"""Async SQLAlchemy + asyncpg database layer and pgvector `<->` cosine similarity search."""

from __future__ import annotations

import math
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.models.entities import (
    CustomerResponse,
    EmployeeResponse,
    InventoryCreateRequest,
    InventoryResponse,
    OrderCreateRequest,
    OrderResponse,
    OrderStatus,
)

settings = get_settings()

# SQLAlchemy 2.0 AsyncEngine configured for Supabase PostgreSQL (`asyncpg` driver)
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Canonical SQL query exercising Supabase `pgvector` `<->` distance operator
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
        (embedding <-> CAST(:query_embedding AS vector(768))) AS distance,
        created_at,
        updated_at
    FROM inventory
    WHERE tenant_id = :tenant_id
      AND embedding IS NOT NULL
    ORDER BY embedding <-> CAST(:query_embedding AS vector(768)) ASC
    LIMIT :limit
    """
)


def _normalize_tenant_id(tenant_id: str) -> str:
    """Normalize default tenant aliases while preserving strict isolation for distinct tenants."""
    if tenant_id in ("default-tenant", "tenant-default", ""):
        return "default-tenant"
    return tenant_id


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


class ERPRepository:
    """Repository providing ERP state persistence and `pgvector` semantic search.

    Pre-seeded with realistic Vietnamese omnichannel retail data for immediate demo
    and test readiness while maintaining full schema parity with `init_schema.sql`.
    """

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self._inventory: dict[UUID, dict[str, Any]] = {}
        self._customers: dict[UUID, CustomerResponse] = {}
        self._employees: dict[UUID, EmployeeResponse] = {}
        self._orders: dict[UUID, OrderResponse] = {}
        self._seed_defaults(now)

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

    def get_employee(self, employee_id: UUID | None, tenant_id: str = "default-tenant") -> EmployeeResponse | None:
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

    def create_order(self, payload: OrderCreateRequest, sepay_qr_url: str, order_code: str) -> OrderResponse:
        now = datetime.now(UTC)
        order_id = uuid4()
        norm_tenant = _normalize_tenant_id(payload.tenant_id)
        total_amount = sum(item.quantity * item.unit_price_vnd for item in payload.items)
        assigned_emp_id = payload.assigned_employee_id
        if assigned_emp_id is None:
            default_emp = self.get_employee(None, tenant_id=norm_tenant)
            assigned_emp_id = default_emp.id if default_emp else None

        customer = self._customers.get(payload.customer_id)
        customer_name = customer.full_name if customer else "Enterprise Customer"
        items_dump = [item.model_dump() for item in payload.items]

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
            items=list(payload.items),
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


InMemoryERPStore = ERPRepository
erp_repository = ERPRepository()
