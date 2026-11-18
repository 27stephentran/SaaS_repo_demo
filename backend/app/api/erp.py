"""ERP REST API endpoints for Inventory, Vector Search (`pgvector`), Orders, Customers, and Employees."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.core.database import erp_repository
from app.models.entities import (
    CustomerResponse,
    EmployeeResponse,
    InventoryCreateRequest,
    InventoryResponse,
    InventorySearchRequest,
    OrderCreateRequest,
    OrderResponse,
    OrderStatusUpdateRequest,
)
from app.services.ai_rag import sales_rag_engine
from app.services.integrations import sepay_client

router = APIRouter(prefix="/api/v1/erp", tags=["ERP"])


@router.get("/inventory", response_model=list[InventoryResponse])
async def list_inventory(
    tenant_id: str = Query(default="default-tenant"),
) -> list[InventoryResponse]:
    """List all inventory items for the specified tenant."""
    return erp_repository.list_inventory(tenant_id=tenant_id)


@router.post(
    "/inventory",
    response_model=InventoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inventory_item(payload: InventoryCreateRequest) -> InventoryResponse:
    """Create a new inventory item and index its 768-dim `text-embedding-004` vector."""
    embedding = await sales_rag_engine.embed_text(f"{payload.name} {payload.description}")
    return erp_repository.create_inventory_item(payload=payload, embedding=embedding)


@router.post("/inventory/search", response_model=list[InventoryResponse])
async def search_inventory(payload: InventorySearchRequest) -> list[InventoryResponse]:
    """Perform semantic vector similarity search (`<->` cosine distance) over product inventory."""
    query_embedding = await sales_rag_engine.embed_text(payload.query)
    return erp_repository.search_inventory_by_embedding(
        query_embedding=query_embedding,
        query_text=payload.query,
        tenant_id=payload.tenant_id,
        limit=payload.limit,
        embedding_fn=sales_rag_engine.embed_text_sync,
    )


@router.get("/inventory/search", response_model=list[InventoryResponse])
async def search_inventory_get(
    q: str = Query(..., min_length=1, description="Natural-language search query"),
    tenant_id: str = Query(default="default-tenant"),
    limit: int = Query(default=5, ge=1, le=25),
) -> list[InventoryResponse]:
    """Perform semantic vector similarity search (`<->` cosine distance) via GET query parameter."""
    query_embedding = await sales_rag_engine.embed_text(q)
    return erp_repository.search_inventory_by_embedding(
        query_embedding=query_embedding,
        query_text=q,
        tenant_id=tenant_id,
        limit=limit,
        embedding_fn=sales_rag_engine.embed_text_sync,
    )


@router.get("/orders", response_model=list[OrderResponse])
async def list_orders(
    tenant_id: str = Query(default="default-tenant"),
) -> list[OrderResponse]:
    """List all orders for the specified tenant."""
    return erp_repository.list_orders(tenant_id=tenant_id)


@router.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(payload: OrderCreateRequest) -> OrderResponse:
    """Create a new order and generate a dynamic SePay VietQR payment URL."""
    existing_orders = erp_repository.list_orders(tenant_id=payload.tenant_id)
    order_code = f"DH{1001 + len(existing_orders)}"
    total_amount_vnd = sum(item.quantity * item.unit_price_vnd for item in payload.items)
    qr_url = sepay_client.generate_vietqr_url(
        amount_vnd=total_amount_vnd,
        order_code=order_code,
    )
    return erp_repository.create_order(
        payload=payload,
        sepay_qr_url=qr_url,
        order_code=order_code,
    )


@router.patch("/orders/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: UUID,
    payload: OrderStatusUpdateRequest,
) -> OrderResponse:
    """Transition an order lifecycle status from the Nuxt 3 ERP Kanban board."""
    updated = erp_repository.update_order_status(order_id=order_id, new_status=payload.status)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        )
    return updated


@router.get("/customers", response_model=list[CustomerResponse])
async def list_customers(
    tenant_id: str = Query(default="default-tenant"),
) -> list[CustomerResponse]:
    """List all omnichannel customers for the specified tenant."""
    return erp_repository.list_customers(tenant_id=tenant_id)


@router.get("/employees", response_model=list[EmployeeResponse])
async def list_employees(
    tenant_id: str = Query(default="default-tenant"),
) -> list[EmployeeResponse]:
    """List all fulfillment staff members (`telegram_user_id`) for the specified tenant."""
    return erp_repository.list_employees(tenant_id=tenant_id)
