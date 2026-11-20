"""Server-authoritative order service, atomic inventory reservation/restock, and state machine."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text

from app.core.auth import normalize_tenant_id
from app.core.config import get_settings
from app.core.database import PGVECTOR_SIMILARITY_SQL, erp_repository, tenant_db_session
from app.models.entities import (
    CustomerResponse,
    EmployeeResponse,
    ErpMetricsResponse,
    InventoryCreateRequest,
    InventoryResponse,
    OrderCreateRequest,
    OrderLineItem,
    OrderLineItemRequest,
    OrderResponse,
    OrderStatus,
    WebhookLogResponse,
)
from app.services.ai_rag import sales_rag_engine
from app.services.integrations import sepay_client

ALLOWED_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_PAYMENT: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.FULFILLING, OrderStatus.CANCELLED}),
    OrderStatus.FULFILLING: frozenset({OrderStatus.COMPLETED, OrderStatus.CANCELLED}),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _aggregate_sku_quantities(items: list[OrderLineItem]) -> list[OrderLineItem]:
    """Aggregate quantities per SKU so atomic stock checks cannot be bypassed by duplicate lines."""
    totals: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    prices: dict[str, int] = {}
    for item in items:
        totals[item.sku] += item.quantity
        names[item.sku] = item.name
        prices[item.sku] = item.unit_price_vnd
    return [
        OrderLineItem(
            sku=sku,
            name=names[sku],
            quantity=qty,
            unit_price_vnd=prices[sku],
        )
        for sku, qty in sorted(totals.items(), key=lambda pair: pair[0])
    ]


class OrderService:
    """Service layer enforcing server-authoritative pricing, stock invariants, and RLS-safe ops."""

    async def list_inventory(self, tenant_id: str) -> list[InventoryResponse]:
        """List tenant-scoped inventory items using PostgreSQL RLS when active."""
        norm_tenant = normalize_tenant_id(tenant_id)
        if await erp_repository.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
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
                        (embedding IS NOT NULL) AS has_embedding,
                        created_at,
                        updated_at
                    FROM inventory
                    WHERE tenant_id = :tenant_id
                    ORDER BY created_at ASC, sku ASC
                    """
                )
                result = await session.execute(stmt, {"tenant_id": norm_tenant})
                rows = result.mappings().all()
                return [InventoryResponse.model_validate(dict(row)) for row in rows]

        return erp_repository.list_inventory(tenant_id=norm_tenant)

    async def create_inventory_item(
        self,
        payload: InventoryCreateRequest,
        embedding: list[float],
        tenant_id: str,
    ) -> InventoryResponse:
        """Create a tenant-scoped inventory item with its 768-dim embedding."""
        norm_tenant = normalize_tenant_id(tenant_id)
        scoped_payload = payload.model_copy(update={"tenant_id": norm_tenant})

        if await erp_repository.is_postgres_active():
            vector_literal = "[" + ",".join(str(val) for val in embedding) + "]"
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    INSERT INTO inventory (
                        tenant_id,
                        sku,
                        name,
                        description,
                        category,
                        price_vnd,
                        stock_quantity,
                        embedding
                    )
                    VALUES (
                        :tenant_id,
                        :sku,
                        :name,
                        :description,
                        :category,
                        :price_vnd,
                        :stock_quantity,
                        CAST(:embedding AS vector(768))
                    )
                    RETURNING
                        id,
                        tenant_id,
                        sku,
                        name,
                        description,
                        category,
                        price_vnd,
                        stock_quantity,
                        (embedding IS NOT NULL) AS has_embedding,
                        created_at,
                        updated_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "sku": scoped_payload.sku,
                        "name": scoped_payload.name,
                        "description": scoped_payload.description,
                        "category": scoped_payload.category,
                        "price_vnd": scoped_payload.price_vnd,
                        "stock_quantity": scoped_payload.stock_quantity,
                        "embedding": vector_literal,
                    },
                )
                row = dict(result.mappings().one())
                return InventoryResponse.model_validate(row)

        return erp_repository.create_inventory_item(payload=scoped_payload, embedding=embedding)

    async def search_inventory(
        self,
        query_embedding: list[float],
        query_text: str,
        tenant_id: str,
        limit: int = 5,
    ) -> list[InventoryResponse]:
        """Perform tenant-scoped semantic vector search over inventory."""
        norm_tenant = normalize_tenant_id(tenant_id)
        if await erp_repository.is_postgres_active():
            vector_literal = "[" + ",".join(str(val) for val in query_embedding) + "]"
            async with tenant_db_session(norm_tenant) as session:
                result = await session.execute(
                    PGVECTOR_SIMILARITY_SQL,
                    {
                        "query_embedding": vector_literal,
                        "tenant_id": norm_tenant,
                        "limit": limit,
                    },
                )
                rows = result.mappings().all()
                items: list[InventoryResponse] = []
                for row in rows:
                    data = dict(row)
                    items.append(
                        InventoryResponse(
                            id=data["id"],
                            tenant_id=data["tenant_id"],
                            sku=data["sku"],
                            name=data["name"],
                            description=data["description"],
                            category=data.get("category", "Hardware"),
                            price_vnd=int(data["price_vnd"]),
                            stock_quantity=int(data["stock_quantity"]),
                            has_embedding=True,
                            similarity_distance=round(float(data["distance"]), 6),
                            similarity_score=round(float(data["similarity_score"]), 4),
                            created_at=data["created_at"],
                            updated_at=data["updated_at"],
                        )
                    )
                return items

        return erp_repository.search_inventory_by_embedding(
            query_embedding=query_embedding,
            query_text=query_text,
            tenant_id=norm_tenant,
            limit=limit,
            embedding_fn=sales_rag_engine.embed_text_sync,
        )

    async def list_customers(self, tenant_id: str) -> list[CustomerResponse]:
        """List customers scoped to the authenticated tenant."""
        norm_tenant = normalize_tenant_id(tenant_id)
        if await erp_repository.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT id, tenant_id, meta_psid, full_name, phone, created_at
                    FROM customers
                    WHERE tenant_id = :tenant_id
                    ORDER BY created_at ASC
                    """
                )
                result = await session.execute(stmt, {"tenant_id": norm_tenant})
                rows = result.mappings().all()
                return [CustomerResponse.model_validate(dict(row)) for row in rows]

        return erp_repository.list_customers(tenant_id=norm_tenant)

    async def list_employees(self, tenant_id: str) -> list[EmployeeResponse]:
        """List fulfillment employees scoped to the authenticated tenant."""
        norm_tenant = normalize_tenant_id(tenant_id)
        if await erp_repository.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT id, tenant_id, full_name, role, telegram_user_id, is_active, created_at
                    FROM employees
                    WHERE tenant_id = :tenant_id
                    ORDER BY created_at ASC
                    """
                )
                result = await session.execute(stmt, {"tenant_id": norm_tenant})
                rows = result.mappings().all()
                return [EmployeeResponse.model_validate(dict(row)) for row in rows]

        return erp_repository.list_employees(tenant_id=norm_tenant)

    async def _lookup_catalog_by_sku(
        self,
        tenant_id: str,
        skus: list[str],
    ) -> dict[str, InventoryResponse]:
        """Load authoritative catalog items for `(tenant_id, sku)`."""
        inventory_items = await self.list_inventory(tenant_id=tenant_id)
        catalog_by_sku = {item.sku: item for item in inventory_items}
        missing = [sku for sku in skus if sku not in catalog_by_sku]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown inventory SKU(s): {', '.join(missing)}",
            )
        return catalog_by_sku

    async def _get_order(self, order_id: UUID, tenant_id: str) -> OrderResponse | None:
        norm_tenant = normalize_tenant_id(tenant_id)
        if await erp_repository.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        o.id,
                        o.tenant_id,
                        o.order_code,
                        o.customer_id,
                        COALESCE(c.full_name, 'Enterprise Customer') AS customer_name,
                        o.assigned_employee_id,
                        o.status,
                        o.total_amount_vnd,
                        o.sepay_qr_url,
                        o.sepay_transaction_id,
                        o.items_json,
                        o.created_at,
                        o.updated_at
                    FROM orders o
                    LEFT JOIN customers c ON c.id = o.customer_id AND c.tenant_id = o.tenant_id
                    WHERE o.id = :order_id
                      AND o.tenant_id = :tenant_id
                    LIMIT 1
                    """
                )
                result = await session.execute(
                    stmt,
                    {"order_id": order_id, "tenant_id": norm_tenant},
                )
                row = result.mappings().first()
                return OrderResponse.model_validate(dict(row)) if row else None

        for existing in erp_repository.list_orders(tenant_id=norm_tenant):
            if existing.id == order_id:
                return existing
        return None

    async def expire_stale_pending_orders(
        self,
        tenant_id: str,
        now: datetime | None = None,
    ) -> list[OrderResponse]:
        """Cancel stale `PENDING_PAYMENT` orders past `order_payment_ttl_seconds` and restore stock."""
        norm_tenant = normalize_tenant_id(tenant_id)
        settings = get_settings()
        ttl_seconds = settings.order_payment_ttl_seconds
        ref_now = _ensure_utc(now or datetime.now(UTC))
        expired: list[OrderResponse] = []

        if await erp_repository.is_postgres_active():
            stale_rows: list[dict[str, Any]] = []
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE orders
                    SET status = 'CANCELLED',
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND status = 'PENDING_PAYMENT'
                      AND EXTRACT(EPOCH FROM (:ref_now - created_at)) > :ttl_seconds
                    RETURNING
                        id,
                        tenant_id,
                        order_code,
                        customer_id,
                        assigned_employee_id,
                        status,
                        total_amount_vnd,
                        sepay_qr_url,
                        sepay_transaction_id,
                        items_json,
                        created_at,
                        updated_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "tenant_id": norm_tenant,
                        "ref_now": ref_now,
                        "ttl_seconds": ttl_seconds,
                    },
                )
                stale_rows = [dict(r) for r in result.mappings().all()]

            for row in stale_rows:
                order_obj = OrderResponse.model_validate(row)
                await erp_repository.restore_stock(
                    items=order_obj.items or order_obj.items_json,
                    tenant_id=norm_tenant,
                )
                expired.append(order_obj)
            return expired

        for order in list(erp_repository.list_orders(tenant_id=norm_tenant)):
            if order.status != OrderStatus.PENDING_PAYMENT:
                continue
            age_seconds = (ref_now - _ensure_utc(order.created_at)).total_seconds()
            if age_seconds > ttl_seconds:
                updated = erp_repository.update_order_status(
                    order_id=order.id,
                    new_status=OrderStatus.CANCELLED,
                    tenant_id=norm_tenant,
                )
                if updated is not None:
                    await erp_repository.restore_stock(
                        items=updated.items or updated.items_json,
                        tenant_id=norm_tenant,
                    )
                    expired.append(updated)
        return expired

    async def list_orders(self, tenant_id: str) -> list[OrderResponse]:
        """List tenant-scoped orders after expiring stale unpaid orders."""
        norm_tenant = normalize_tenant_id(tenant_id)
        await self.expire_stale_pending_orders(tenant_id=norm_tenant)

        if await erp_repository.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    SELECT
                        o.id,
                        o.tenant_id,
                        o.order_code,
                        o.customer_id,
                        COALESCE(c.full_name, 'Enterprise Customer') AS customer_name,
                        o.assigned_employee_id,
                        o.status,
                        o.total_amount_vnd,
                        o.sepay_qr_url,
                        o.sepay_transaction_id,
                        o.items_json,
                        o.created_at,
                        o.updated_at
                    FROM orders o
                    LEFT JOIN customers c ON c.id = o.customer_id AND c.tenant_id = o.tenant_id
                    WHERE o.tenant_id = :tenant_id
                    ORDER BY o.created_at DESC
                    """
                )
                result = await session.execute(stmt, {"tenant_id": norm_tenant})
                rows = result.mappings().all()
                return [OrderResponse.model_validate(dict(row)) for row in rows]

        return erp_repository.list_orders(tenant_id=norm_tenant)

    async def create_order(
        self,
        payload: OrderCreateRequest,
        tenant_id: str,
    ) -> OrderResponse:
        """Create an order using server-authoritative catalog prices and atomic stock decrement."""
        norm_tenant = normalize_tenant_id(tenant_id)
        settings = get_settings()

        requested_skus = [item.sku for item in payload.items]
        catalog_by_sku = await self._lookup_catalog_by_sku(
            tenant_id=norm_tenant,
            skus=requested_skus,
        )

        # Build server-authoritative line items ignoring any client-supplied unit_price_vnd/name
        authoritative_items: list[OrderLineItem] = []
        for req_item in payload.items:
            catalog_item = catalog_by_sku[req_item.sku]
            authoritative_items.append(
                OrderLineItem(
                    sku=catalog_item.sku,
                    name=catalog_item.name,
                    quantity=req_item.quantity,
                    unit_price_vnd=catalog_item.price_vnd,
                )
            )

        total_amount_vnd = sum(
            item.quantity * item.unit_price_vnd for item in authoritative_items
        )
        aggregated_stock_items = _aggregate_sku_quantities(authoritative_items)

        decremented = await erp_repository.atomic_decrement_stock(
            items=aggregated_stock_items,
            tenant_id=norm_tenant,
        )
        if not decremented:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Insufficient inventory stock for one or more requested SKUs",
            )

        try:
            order_code = await erp_repository.next_order_code(
                tenant_id=norm_tenant,
                prefix=settings.sepay_payment_prefix or "DH",
            )
            qr_url = sepay_client.generate_vietqr_url(
                amount_vnd=total_amount_vnd,
                order_code=order_code,
            )

            if await erp_repository.is_postgres_active():
                async with tenant_db_session(norm_tenant) as session:
                    cust_res = await session.execute(
                        text(
                            """
                            SELECT full_name
                            FROM customers
                            WHERE id = :customer_id
                              AND tenant_id = :tenant_id
                            LIMIT 1
                            """
                        ),
                        {"customer_id": payload.customer_id, "tenant_id": norm_tenant},
                    )
                    cust_name = cust_res.scalar_one_or_none()
                    if cust_name is None:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Customer {payload.customer_id} not found for tenant",
                        )

                    assigned_emp_id = payload.assigned_employee_id
                    if assigned_emp_id is not None:
                        emp_check = await session.execute(
                            text(
                                """
                                SELECT id
                                FROM employees
                                WHERE id = :emp_id
                                  AND tenant_id = :tenant_id
                                  AND is_active = TRUE
                                LIMIT 1
                                """
                            ),
                            {"emp_id": assigned_emp_id, "tenant_id": norm_tenant},
                        )
                        if emp_check.scalar_one_or_none() is None:
                            raise HTTPException(
                                status_code=status.HTTP_404_NOT_FOUND,
                                detail="Assigned employee not found for tenant",
                            )
                    else:
                        emp_res = await session.execute(
                            text(
                                """
                                SELECT id
                                FROM employees
                                WHERE tenant_id = :tenant_id
                                  AND is_active = TRUE
                                ORDER BY created_at ASC
                                LIMIT 1
                                """
                            ),
                            {"tenant_id": norm_tenant},
                        )
                        assigned_emp_id = emp_res.scalar_one_or_none()

                    items_dump = [item.model_dump() for item in authoritative_items]
                    insert_stmt = text(
                        """
                        INSERT INTO orders (
                            tenant_id,
                            order_code,
                            customer_id,
                            assigned_employee_id,
                            status,
                            total_amount_vnd,
                            sepay_qr_url,
                            items_json
                        )
                        VALUES (
                            :tenant_id,
                            :order_code,
                            :customer_id,
                            :assigned_employee_id,
                            'PENDING_PAYMENT',
                            :total_amount_vnd,
                            :sepay_qr_url,
                            CAST(:items_json AS jsonb)
                        )
                        RETURNING
                            id,
                            tenant_id,
                            order_code,
                            customer_id,
                            assigned_employee_id,
                            status,
                            total_amount_vnd,
                            sepay_qr_url,
                            sepay_transaction_id,
                            items_json,
                            created_at,
                            updated_at
                        """
                    )
                    order_res = await session.execute(
                        insert_stmt,
                        {
                            "tenant_id": norm_tenant,
                            "order_code": order_code,
                            "customer_id": payload.customer_id,
                            "assigned_employee_id": assigned_emp_id,
                            "total_amount_vnd": total_amount_vnd,
                            "sepay_qr_url": qr_url,
                            "items_json": json.dumps(items_dump),
                        },
                    )
                    row = dict(order_res.mappings().one())
                    row["customer_name"] = cust_name
                    return OrderResponse.model_validate(row)

            # Local fallback: ensure a foreign-tenant customer ID cannot leak another tenant's name
            foreign_customer = erp_repository._customers.get(payload.customer_id)
            if (
                foreign_customer is not None
                and normalize_tenant_id(foreign_customer.tenant_id) != norm_tenant
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Customer {payload.customer_id} not found for tenant",
                )

            if payload.assigned_employee_id is not None:
                tenant_employees = erp_repository.list_employees(tenant_id=norm_tenant)
                matched_emp = next(
                    (
                        emp
                        for emp in tenant_employees
                        if emp.id == payload.assigned_employee_id and emp.is_active
                    ),
                    None,
                )
                if matched_emp is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Assigned employee not found for tenant",
                    )

            authoritative_payload = OrderCreateRequest(
                tenant_id=norm_tenant,
                customer_id=payload.customer_id,
                assigned_employee_id=payload.assigned_employee_id,
                items=[
                    OrderLineItemRequest(
                        sku=item.sku,
                        quantity=item.quantity,
                        unit_price_vnd=item.unit_price_vnd,
                        name=item.name,
                    )
                    for item in authoritative_items
                ],
            )
            return erp_repository.create_order(
                payload=authoritative_payload,
                sepay_qr_url=qr_url,
                order_code=order_code,
            )
        except Exception:
            await erp_repository.restore_stock(
                items=aggregated_stock_items,
                tenant_id=norm_tenant,
            )
            raise

    async def update_order_status(
        self,
        order_id: UUID,
        new_status: OrderStatus,
        tenant_id: str,
        actor_role: str = "admin",
    ) -> OrderResponse:
        """Enforce explicit order status state machine, payment authority, and cancellation restock."""
        norm_tenant = normalize_tenant_id(tenant_id)
        await self.expire_stale_pending_orders(tenant_id=norm_tenant)

        existing = await self._get_order(order_id=order_id, tenant_id=norm_tenant)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order {order_id} not found",
            )

        if existing.status == new_status:
            return existing

        allowed = ALLOWED_ORDER_TRANSITIONS.get(existing.status, frozenset())
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Invalid order status transition from "
                    f"{existing.status.value} to {new_status.value}"
                ),
            )

        if (
            existing.status == OrderStatus.PENDING_PAYMENT
            and new_status == OrderStatus.PAID
            and actor_role.strip().lower() != "admin"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Manual PAID transition requires admin reconciliation; "
                    "automatic settlement uses SePay webhook"
                ),
            )

        updated: OrderResponse | None = None
        if await erp_repository.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                stmt = text(
                    """
                    UPDATE orders
                    SET status = :new_status,
                        updated_at = NOW()
                    WHERE id = :order_id
                      AND tenant_id = :tenant_id
                      AND status = :expected_status
                    RETURNING
                        id,
                        tenant_id,
                        order_code,
                        customer_id,
                        assigned_employee_id,
                        status,
                        total_amount_vnd,
                        sepay_qr_url,
                        sepay_transaction_id,
                        items_json,
                        created_at,
                        updated_at
                    """
                )
                result = await session.execute(
                    stmt,
                    {
                        "new_status": new_status.value,
                        "order_id": order_id,
                        "tenant_id": norm_tenant,
                        "expected_status": existing.status.value,
                    },
                )
                row = result.mappings().first()
                if row is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Order status changed concurrently",
                    )
                row_dict = dict(row)
                row_dict["customer_name"] = existing.customer_name
                updated = OrderResponse.model_validate(row_dict)
        else:
            updated = erp_repository.update_order_status(
                order_id=order_id,
                new_status=new_status,
                tenant_id=norm_tenant,
            )

        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order {order_id} not found",
            )

        if new_status == OrderStatus.CANCELLED:
            await erp_repository.restore_stock(
                items=updated.items or updated.items_json,
                tenant_id=norm_tenant,
            )

        return updated

    async def list_webhook_logs(
        self,
        tenant_id: str,
        limit: int = 50,
    ) -> list[WebhookLogResponse]:
        """List tenant-scoped SePay webhook audit logs ordered newest-first."""
        norm_tenant = normalize_tenant_id(tenant_id)
        return await erp_repository.list_webhook_logs(tenant_id=norm_tenant, limit=limit)

    async def get_metrics(self, tenant_id: str) -> ErpMetricsResponse:
        """Compute deterministic tenant-scoped ERP KPIs across inventory, orders, and webhooks."""
        norm_tenant = normalize_tenant_id(tenant_id)
        orders_by_status: dict[str, int] = {status_val.value: 0 for status_val in OrderStatus}

        if await erp_repository.is_postgres_active():
            async with tenant_db_session(norm_tenant) as session:
                inv_res = await session.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS active_inventory_skus,
                            COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS vectorized_skus
                        FROM inventory
                        WHERE tenant_id = :tenant_id
                        """
                    ),
                    {"tenant_id": norm_tenant},
                )
                inv_row = inv_res.mappings().one()
                active_inventory_skus = int(inv_row["active_inventory_skus"] or 0)
                vectorized_skus = int(inv_row["vectorized_skus"] or 0)

                order_res = await session.execute(
                    text(
                        """
                        SELECT status, COUNT(*) AS status_count
                        FROM orders
                        WHERE tenant_id = :tenant_id
                        GROUP BY status
                        """
                    ),
                    {"tenant_id": norm_tenant},
                )
                for row in order_res.mappings().all():
                    status_key = str(row["status"])
                    orders_by_status[status_key] = int(row["status_count"] or 0)

                webhook_res = await session.execute(
                    text(
                        """
                        SELECT COUNT(*) AS webhook_events_count
                        FROM sepay_webhook_logs
                        WHERE tenant_id = :tenant_id
                        """
                    ),
                    {"tenant_id": norm_tenant},
                )
                webhook_events_count = int(webhook_res.scalar_one() or 0)
        else:
            inventory_items = erp_repository.list_inventory(tenant_id=norm_tenant)
            active_inventory_skus = len(inventory_items)
            vectorized_skus = sum(1 for item in inventory_items if item.has_embedding)

            for order in erp_repository.list_orders(tenant_id=norm_tenant):
                status_key = order.status.value
                orders_by_status[status_key] = orders_by_status.get(status_key, 0) + 1

            webhook_events_count = sum(
                1
                for entry in erp_repository._sepay_webhook_logs.values()
                if normalize_tenant_id(str(entry["tenant_id"])) == norm_tenant
            )

        pending_vietqr_orders = orders_by_status.get(OrderStatus.PENDING_PAYMENT.value, 0)
        paid_and_dispatched_orders = (
            orders_by_status.get(OrderStatus.PAID.value, 0)
            + orders_by_status.get(OrderStatus.FULFILLING.value, 0)
            + orders_by_status.get(OrderStatus.COMPLETED.value, 0)
        )
        total_orders = sum(orders_by_status.values())

        return ErpMetricsResponse(
            tenant_id=norm_tenant,
            active_inventory_skus=active_inventory_skus,
            vectorized_skus=vectorized_skus,
            pending_vietqr_orders=pending_vietqr_orders,
            paid_and_dispatched_orders=paid_and_dispatched_orders,
            total_orders=total_orders,
            webhook_events_count=webhook_events_count,
            orders_by_status=orders_by_status,
        )


order_service = OrderService()
