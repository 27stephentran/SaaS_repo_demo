"""ERP REST API endpoints for Inventory, Vector Search (`pgvector`), Orders, Customers, and Employees."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status

from app.core.auth import AuthenticatedUser, get_current_user, resolve_authenticated_tenant
from app.models.entities import (
    CustomerResponse,
    EmployeeResponse,
    ErpMetricsResponse,
    InventoryCreateRequest,
    InventoryResponse,
    InventorySearchRequest,
    OrderCreateRequest,
    OrderResponse,
    OrderStatusUpdateRequest,
    WebhookLogResponse,
)
from app.services.ai_rag import sales_rag_engine
from app.services.order_service import order_service

router = APIRouter(prefix="/api/v1/erp", tags=["ERP"])

CurrentUserDep = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.get("/inventory", response_model=list[InventoryResponse])
async def list_inventory(
    current_user: CurrentUserDep,
    tenant_id: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[InventoryResponse]:
    """List all inventory items for the authenticated tenant."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    return await order_service.list_inventory(tenant_id=effective_tenant)


@router.post(
    "/inventory",
    response_model=InventoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inventory_item(
    payload: InventoryCreateRequest,
    current_user: CurrentUserDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> InventoryResponse:
    """Create a new inventory item and index its 768-dim `text-embedding-004` vector."""
    requested_tenant = (
        payload.tenant_id if "tenant_id" in payload.model_fields_set else None
    )
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=requested_tenant,
        header_tenant_id=x_tenant_id,
    )
    embedding = await sales_rag_engine.embed_text(f"{payload.name} {payload.description}")
    return await order_service.create_inventory_item(
        payload=payload,
        embedding=embedding,
        tenant_id=effective_tenant,
    )


@router.post("/inventory/search", response_model=list[InventoryResponse])
async def search_inventory(
    payload: InventorySearchRequest,
    current_user: CurrentUserDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[InventoryResponse]:
    """Perform semantic vector similarity search (`<=>` cosine distance) over product inventory."""
    requested_tenant = (
        payload.tenant_id if "tenant_id" in payload.model_fields_set else None
    )
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=requested_tenant,
        header_tenant_id=x_tenant_id,
    )
    query_embedding = await sales_rag_engine.embed_text(payload.query)
    return await order_service.search_inventory(
        query_embedding=query_embedding,
        query_text=payload.query,
        tenant_id=effective_tenant,
        limit=payload.limit,
    )


@router.get("/inventory/search", response_model=list[InventoryResponse])
async def search_inventory_get(
    current_user: CurrentUserDep,
    q: str = Query(..., min_length=1, description="Natural-language search query"),
    tenant_id: str | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=25),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[InventoryResponse]:
    """Perform semantic vector similarity search (`<=>` cosine distance) via GET query parameter."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    query_embedding = await sales_rag_engine.embed_text(q)
    return await order_service.search_inventory(
        query_embedding=query_embedding,
        query_text=q,
        tenant_id=effective_tenant,
        limit=limit,
    )


@router.get("/orders", response_model=list[OrderResponse])
async def list_orders(
    current_user: CurrentUserDep,
    tenant_id: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[OrderResponse]:
    """List all orders for the authenticated tenant."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    return await order_service.list_orders(tenant_id=effective_tenant)


@router.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    payload: OrderCreateRequest,
    current_user: CurrentUserDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> OrderResponse:
    """Create a new server-authoritative order and generate a dynamic SePay VietQR URL."""
    requested_tenant = (
        payload.tenant_id if "tenant_id" in payload.model_fields_set else None
    )
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=requested_tenant,
        header_tenant_id=x_tenant_id,
    )
    return await order_service.create_order(
        payload=payload,
        tenant_id=effective_tenant,
    )


@router.patch("/orders/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: UUID,
    payload: OrderStatusUpdateRequest,
    current_user: CurrentUserDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> OrderResponse:
    """Transition an order lifecycle status from the Nuxt 3 ERP Kanban board."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    return await order_service.update_order_status(
        order_id=order_id,
        new_status=payload.status,
        tenant_id=effective_tenant,
        actor_role=current_user.role,
    )


@router.get("/customers", response_model=list[CustomerResponse])
async def list_customers(
    current_user: CurrentUserDep,
    tenant_id: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[CustomerResponse]:
    """List all omnichannel customers for the authenticated tenant."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    return await order_service.list_customers(tenant_id=effective_tenant)


@router.get("/employees", response_model=list[EmployeeResponse])
async def list_employees(
    current_user: CurrentUserDep,
    tenant_id: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[EmployeeResponse]:
    """List all fulfillment staff members (`telegram_user_id`) for the authenticated tenant."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    return await order_service.list_employees(tenant_id=effective_tenant)


@router.get("/webhooks/logs", response_model=list[WebhookLogResponse])
async def list_webhook_logs(
    current_user: CurrentUserDep,
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[WebhookLogResponse]:
    """List tenant-scoped SePay webhook audit logs ordered newest-first."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    return await order_service.list_webhook_logs(
        tenant_id=effective_tenant,
        limit=limit,
    )


@router.get("/metrics", response_model=ErpMetricsResponse)
async def get_erp_metrics(
    current_user: CurrentUserDep,
    tenant_id: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> ErpMetricsResponse:
    """Return tenant-scoped ERP KPI telemetry for inventory, orders, and webhooks."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    return await order_service.get_metrics(tenant_id=effective_tenant)
