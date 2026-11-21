"""ERP REST API endpoints for Inventory, Vector Search (`pgvector`), Orders, Customers, Staff, Analytics, and Chats."""

from __future__ import annotations

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from app.core.auth import (
    AuthenticatedUser,
    hash_password,
    require_live_api,
    require_roles,
    resolve_authenticated_tenant,
    resolve_company_scope,
)
from app.core.database import erp_repository
from app.models.entities import (
    ChannelConversationResponse,
    ChannelMessageResponse,
    ChatReplyRequest,
    CustomerResponse,
    EmployeeResponse,
    ErpMetricsResponse,
    InventoryCreateRequest,
    InventoryResponse,
    InventorySearchRequest,
    ManagerDashboardResponse,
    OrderCreateRequest,
    OrderResponse,
    OrderStatus,
    OrderStatusUpdateRequest,
    OrderTelegramNotifyRequest,
    RestoreResponse,
    SePayCheckoutFormResponse,
    SePayCheckoutRequest,
    SoftDeleteResponse,
    StaffCreateRequest,
    StaffCreateResponse,
    StaffMemberSummaryResponse,
    TelegramDispatchResponse,
    WebhookLogResponse,
)
from app.services.ai_rag import sales_rag_engine
from app.services.integrations import (
    meta_graph_client,
    sepay_client,
    telegram_dispatch_client,
)
from app.services.order_service import order_service

router = APIRouter(
    prefix="/api/v1/erp",
    tags=["ERP"],
    dependencies=[Depends(require_live_api)],
)

CurrentUserDep = Annotated[AuthenticatedUser, Depends(require_live_api)]
AdminOrManagerDep = Annotated[AuthenticatedUser, Depends(require_roles("admin", "manager"))]
ManagerOnlyDep = Annotated[AuthenticatedUser, Depends(require_roles("manager"))]


@router.get("/inventory", response_model=list[InventoryResponse])
async def list_inventory(
    current_user: CurrentUserDep,
    include_inactive: bool = Query(default=False),
    tenant_id: str | None = Query(default=None),
    company_id: Annotated[UUID | None, Query()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[InventoryResponse]:
    """List inventory items for the authenticated tenant and company scope."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    return await order_service.list_inventory(
        tenant_id=effective_tenant,
        include_inactive=include_inactive,
        company_id=effective_company,
    )


@router.post(
    "/inventory",
    response_model=InventoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inventory_item(
    payload: InventoryCreateRequest,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
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
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=payload.company_id,
        header_company_id=x_company_id,
    )
    embedding = await sales_rag_engine.embed_text(f"{payload.name} {payload.description}")
    return await order_service.create_inventory_item(
        payload=payload,
        embedding=embedding,
        tenant_id=effective_tenant,
        company_id=effective_company,
    )


@router.delete("/inventory/{item_id}", response_model=SoftDeleteResponse)
async def soft_delete_inventory_item(
    item_id: UUID,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> SoftDeleteResponse:
    """Soft-delete an inventory item (`is_active=False`, `deactivated_at=NOW()`)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    return await order_service.soft_delete_inventory_item(
        item_id=item_id,
        tenant_id=effective_tenant,
        company_id=effective_company,
    )


@router.post("/inventory/{item_id}/restore", response_model=RestoreResponse)
async def restore_inventory_item(
    item_id: UUID,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> RestoreResponse:
    """Restore a soft-deleted inventory item (`is_active=True`, `deactivated_at=None`)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    return await order_service.restore_inventory_item(
        item_id=item_id,
        tenant_id=effective_tenant,
        company_id=effective_company,
    )


@router.post("/inventory/search", response_model=list[InventoryResponse])
async def search_inventory(
    payload: InventorySearchRequest,
    current_user: CurrentUserDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
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
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=payload.company_id,
        header_company_id=x_company_id,
    )
    query_embedding = await sales_rag_engine.embed_text(payload.query)
    return await order_service.search_inventory(
        query_embedding=query_embedding,
        query_text=payload.query,
        tenant_id=effective_tenant,
        limit=payload.limit,
        include_inactive=payload.include_inactive,
        company_id=effective_company,
    )


@router.get("/inventory/search", response_model=list[InventoryResponse])
async def search_inventory_get(
    current_user: CurrentUserDep,
    q: str = Query(..., min_length=1, description="Natural-language search query"),
    include_inactive: bool = Query(default=False),
    tenant_id: str | None = Query(default=None),
    company_id: Annotated[UUID | None, Query()] = None,
    limit: int = Query(default=5, ge=1, le=25),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[InventoryResponse]:
    """Perform semantic vector similarity search (`<=>` cosine distance) via GET query parameter."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    query_embedding = await sales_rag_engine.embed_text(q)
    return await order_service.search_inventory(
        query_embedding=query_embedding,
        query_text=q,
        tenant_id=effective_tenant,
        limit=limit,
        include_inactive=include_inactive,
        company_id=effective_company,
    )


@router.get("/orders", response_model=list[OrderResponse])
async def list_orders(
    current_user: CurrentUserDep,
    include_inactive: bool = Query(default=False),
    tenant_id: str | None = Query(default=None),
    company_id: Annotated[UUID | None, Query()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[OrderResponse]:
    """List orders scoped by tenant, company, and staff work-only assignment."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    assigned_emp_id: UUID | None = None
    if current_user.role.strip().lower() == "staff":
        if current_user.employee_id is None:
            return []
        assigned_emp_id = current_user.employee_id

    return await order_service.list_orders(
        tenant_id=effective_tenant,
        include_inactive=include_inactive,
        company_id=effective_company,
        assigned_employee_id=assigned_emp_id,
    )


@router.get("/my-work", response_model=list[OrderResponse])
async def list_my_work(
    current_user: CurrentUserDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[OrderResponse]:
    """List orders explicitly assigned to the authenticated staff/employee user."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    if current_user.employee_id is None:
        return []
    return await order_service.list_orders(
        tenant_id=effective_tenant,
        company_id=effective_company,
        assigned_employee_id=current_user.employee_id,
    )


@router.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    payload: OrderCreateRequest,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> OrderResponse:
    """Create a new server-authoritative order and generate a company-specific SePay VietQR URL."""
    requested_tenant = (
        payload.tenant_id if "tenant_id" in payload.model_fields_set else None
    )
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=requested_tenant,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=payload.company_id,
        header_company_id=x_company_id,
    )
    return await order_service.create_order(
        payload=payload,
        tenant_id=effective_tenant,
        company_id=effective_company,
    )


@router.patch("/orders/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: UUID,
    payload: OrderStatusUpdateRequest,
    current_user: CurrentUserDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> OrderResponse:
    """Transition an order lifecycle status (enforcing company & staff assignment boundaries)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    if (
        current_user.role.strip().lower() == "staff"
        and current_user.company_id is None
        and x_company_id is None
    ):
        effective_company = None
    else:
        effective_company = resolve_company_scope(
            current_user,
            header_company_id=x_company_id,
        )
    return await order_service.update_order_status(
        order_id=order_id,
        new_status=payload.status,
        tenant_id=effective_tenant,
        actor_role=current_user.role,
        company_id=effective_company,
        assigned_employee_id=current_user.employee_id,
    )


@router.post(
    "/orders/{order_id}/notify-telegram",
    response_model=TelegramDispatchResponse,
)
async def notify_order_telegram(
    order_id: UUID,
    current_user: AdminOrManagerDep,
    payload: Annotated[OrderTelegramNotifyRequest | None, Body()] = None,
    tenant_id: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> TelegramDispatchResponse:
    """Dispatch a staff Telegram fulfillment notification for a live tenant order."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    order = await order_service._get_order(order_id=order_id, tenant_id=effective_tenant)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        )
    if effective_company is not None and order.company_id != effective_company:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-company order notification is forbidden",
        )

    target_chat_id = (
        payload.telegram_user_id.strip()
        if payload is not None and payload.telegram_user_id
        else ""
    )
    if not target_chat_id:
        employees = await order_service.list_employees(
            tenant_id=effective_tenant,
            company_id=order.company_id or effective_company,
        )
        active_employees = [emp for emp in employees if emp.is_active]
        chosen = None
        if order.assigned_employee_id is not None:
            chosen = next(
                (emp for emp in active_employees if emp.id == order.assigned_employee_id),
                None,
            )
        if chosen is None and active_employees:
            chosen = active_employees[0]
        if chosen is not None:
            target_chat_id = chosen.telegram_user_id.strip()

    if not target_chat_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No active employee Telegram chat ID available for order notification",
        )

    items_summary = ", ".join(
        f"{item.quantity}x {item.sku}" for item in order.items
    ) or order.order_code

    result = await telegram_dispatch_client.dispatch_order_alert(
        telegram_user_id=target_chat_id,
        order_code=order.order_code,
        total_amount_vnd=order.total_amount_vnd,
        items_summary=items_summary,
        customer_name=order.customer_name,
        sepay_reference=order.sepay_reference or order.order_code,
    )
    return TelegramDispatchResponse(
        dispatched=bool(result.get("dispatched", False)),
        simulated=bool(result.get("simulated", False)),
        telegram_user_id=str(result.get("telegram_user_id", target_chat_id)),
        order_code=str(result.get("order_code", order.order_code)),
        preview_text=result.get("preview_text") or result.get("text"),
        error=result.get("error"),
        status_code=result.get("status_code"),
    )


@router.post(
    "/orders/{order_id}/sepay-checkout",
    response_model=SePayCheckoutFormResponse,
)
async def create_sepay_checkout(
    order_id: UUID,
    current_user: AdminOrManagerDep,
    payload: Annotated[SePayCheckoutRequest | None, Body()] = None,
    tenant_id: str | None = Query(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> SePayCheckoutFormResponse:
    """Build a server-signed SePay Payment Gateway checkout form for a pending order."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    order = await order_service._get_order(order_id=order_id, tenant_id=effective_tenant)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        )
    if effective_company is not None and order.company_id != effective_company:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-company checkout initiation is forbidden",
        )
    if order.status != OrderStatus.PENDING_PAYMENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order {order.order_code} is {order.status.value}, expected PENDING_PAYMENT",
        )
    if not (sepay_client.pg_merchant_id or "").strip() or not (
        sepay_client.pg_secret_key or ""
    ).strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SePay Payment Gateway credentials are not configured",
        )
    if order.total_amount_vnd <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order {order.order_code} total amount must be positive for checkout",
        )

    try:
        form = sepay_client.build_checkout_form(
            order_code=order.order_code,
            amount_vnd=order.total_amount_vnd,
            description=f"Thanh toan don hang {order.order_code}",
            customer_id=str(order.customer_id),
            payment_method=payload.payment_method if payload else None,
            success_url=payload.success_url if payload else None,
            error_url=payload.error_url if payload else None,
            cancel_url=payload.cancel_url if payload else None,
            lang=payload.lang if payload else "vi",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return SePayCheckoutFormResponse(
        checkout_url=form["checkout_url"],
        fields=form["fields"],
        order_id=order.id,
        order_code=order.order_code,
        total_amount_vnd=order.total_amount_vnd,
    )


@router.get("/customers", response_model=list[CustomerResponse])
async def list_customers(
    current_user: AdminOrManagerDep,
    include_inactive: bool = Query(default=False),
    tenant_id: str | None = Query(default=None),
    company_id: Annotated[UUID | None, Query()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[CustomerResponse]:
    """List omnichannel customers for the authenticated tenant and company scope."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    return await order_service.list_customers(
        tenant_id=effective_tenant,
        include_inactive=include_inactive,
        company_id=effective_company,
    )


@router.delete("/customers/{customer_id}", response_model=SoftDeleteResponse)
async def soft_delete_customer(
    customer_id: UUID,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> SoftDeleteResponse:
    """Soft-delete a customer (`is_active=False`, `deactivated_at=NOW()`)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    return await order_service.soft_delete_customer(
        customer_id=customer_id,
        tenant_id=effective_tenant,
        company_id=effective_company,
    )


@router.post("/customers/{customer_id}/restore", response_model=RestoreResponse)
async def restore_customer(
    customer_id: UUID,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> RestoreResponse:
    """Restore a soft-deleted customer (`is_active=True`, `deactivated_at=None`)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    return await order_service.restore_customer(
        customer_id=customer_id,
        tenant_id=effective_tenant,
        company_id=effective_company,
    )


@router.get("/employees", response_model=list[EmployeeResponse])
async def list_employees(
    current_user: AdminOrManagerDep,
    include_inactive: bool = Query(default=False),
    tenant_id: str | None = Query(default=None),
    company_id: Annotated[UUID | None, Query()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[EmployeeResponse]:
    """List fulfillment staff members (`telegram_user_id`) for the authenticated tenant and company."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    return await order_service.list_employees(
        tenant_id=effective_tenant,
        include_inactive=include_inactive,
        company_id=effective_company,
    )


@router.delete("/employees/{employee_id}", response_model=SoftDeleteResponse)
async def soft_delete_employee(
    employee_id: UUID,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> SoftDeleteResponse:
    """Soft-delete an employee (`is_active=False`, `deactivated_at=NOW()`)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    return await order_service.soft_delete_employee(
        employee_id=employee_id,
        tenant_id=effective_tenant,
        company_id=effective_company,
    )


@router.post("/employees/{employee_id}/restore", response_model=RestoreResponse)
async def restore_employee(
    employee_id: UUID,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> RestoreResponse:
    """Restore a soft-deleted employee (`is_active=True`, `deactivated_at=None`)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    return await order_service.restore_employee(
        employee_id=employee_id,
        tenant_id=effective_tenant,
        company_id=effective_company,
    )


@router.post(
    "/staff",
    response_model=StaffCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff_account(
    payload: StaffCreateRequest,
    current_user: ManagerOnlyDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> StaffCreateResponse:
    """Manager-only endpoint creating a linked `Employee` + `UserAccount(role='staff', must_change_password=True)`."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    assert effective_company is not None

    username = (
        payload.username.strip()
        if payload.username and payload.username.strip()
        else f"staff_{secrets.token_hex(4)}"
    )
    email = (
        payload.email.strip().lower()
        if payload.email and payload.email.strip()
        else f"{username.lower()}@staff.local"
    )
    temporary_password = (
        payload.password
        if payload.password
        else secrets.token_urlsafe(12)
    )

    try:
        created = await erp_repository.create_staff_member(
            tenant_id=effective_tenant,
            company_id=effective_company,
            full_name=payload.full_name,
            role=payload.role,
            telegram_user_id=payload.telegram_user_id,
            email=email,
            username=username,
            password_hash=hash_password(temporary_password),
        )
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email or username already exists for the tenant",
        ) from exc

    return StaffCreateResponse(
        employee_id=created["employee_id"],
        user_id=created["user_id"],
        tenant_id=created["tenant_id"],
        company_id=created["company_id"],
        full_name=created["full_name"],
        role=created["role"],
        telegram_user_id=created["telegram_user_id"],
        email=created["email"],
        username=created["username"],
        temporary_password=temporary_password,
        must_change_password=True,
        created_at=created["created_at"],
    )


@router.get("/staff", response_model=list[StaffMemberSummaryResponse])
async def list_staff_members(
    current_user: AdminOrManagerDep,
    include_inactive: bool = Query(default=False),
    company_id: Annotated[UUID | None, Query()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[StaffMemberSummaryResponse]:
    """List staff members with linked account info and order workload metrics."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    rows = await erp_repository.list_staff_members(
        tenant_id=effective_tenant,
        company_id=effective_company,
        include_inactive=include_inactive,
    )
    return [StaffMemberSummaryResponse.model_validate(row) for row in rows]


@router.get("/dashboard/manager", response_model=ManagerDashboardResponse)
async def get_manager_dashboard(
    current_user: AdminOrManagerDep,
    company_id: Annotated[UUID | None, Query()] = None,
    days: int = Query(default=14, ge=1, le=90),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> ManagerDashboardResponse:
    """Return Manager analytics: revenue, daily income series, staff workload, customers, and chat count."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    return await order_service.get_manager_dashboard(
        tenant_id=effective_tenant,
        company_id=effective_company,
        days=days,
    )


@router.get("/chats", response_model=list[ChannelConversationResponse])
async def list_chats(
    current_user: AdminOrManagerDep,
    channel: str | None = Query(default=None),
    company_id: Annotated[UUID | None, Query()] = None,
    limit: int = Query(default=50, ge=1, le=200),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[ChannelConversationResponse]:
    """List omnichannel conversations scoped to the authenticated tenant and company."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    return await erp_repository.list_channel_conversations(
        tenant_id=effective_tenant,
        company_id=effective_company,
        channel=channel,
        limit=limit,
    )


@router.get("/chats/{conversation_id}/messages", response_model=list[ChannelMessageResponse])
async def list_chat_messages(
    conversation_id: UUID,
    current_user: AdminOrManagerDep,
    limit: int = Query(default=100, ge=1, le=500),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[ChannelMessageResponse]:
    """List chronological messages inside an omnichannel conversation."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    conv = await erp_repository.get_channel_conversation(
        conversation_id=conversation_id,
        tenant_id=effective_tenant,
    )
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    if effective_company is not None and conv.company_id != effective_company:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-company conversation access is forbidden",
        )
    return await erp_repository.list_channel_messages(
        conversation_id=conversation_id,
        tenant_id=effective_tenant,
        company_id=effective_company,
        limit=limit,
    )


@router.post(
    "/chats/{conversation_id}/reply",
    response_model=ChannelMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reply_to_chat(
    conversation_id: UUID,
    payload: ChatReplyRequest,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> ChannelMessageResponse:
    """Send a human Manager/Admin reply to an omnichannel conversation and persist it."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        header_company_id=x_company_id,
    )
    conv = await erp_repository.get_channel_conversation(
        conversation_id=conversation_id,
        tenant_id=effective_tenant,
    )
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    if effective_company is not None and conv.company_id != effective_company:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-company conversation reply is forbidden",
        )

    reply_text = payload.content.strip()
    if conv.channel in ("messenger", "instagram") and conv.external_thread_id:
        await meta_graph_client.send_message(
            recipient_psid=conv.external_thread_id,
            text=reply_text,
        )

    sender_type = "admin" if current_user.role.strip().lower() == "admin" else "manager"
    sender_name = current_user.full_name.strip() or current_user.email or sender_type.title()

    return await erp_repository.append_channel_message(
        tenant_id=effective_tenant,
        company_id=conv.company_id,
        conversation_id=conv.id,
        channel=conv.channel,
        direction="outbound",
        sender_type=sender_type,
        sender_name=sender_name,
        content=reply_text,
        reset_unread=True,
    )


@router.get("/webhooks/logs", response_model=list[WebhookLogResponse])
async def list_webhook_logs(
    current_user: AdminOrManagerDep,
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str | None = Query(default=None),
    company_id: Annotated[UUID | None, Query()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> list[WebhookLogResponse]:
    """List tenant-scoped (and optionally company-scoped) SePay webhook audit logs ordered newest-first."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    return await order_service.list_webhook_logs(
        tenant_id=effective_tenant,
        limit=limit,
        company_id=effective_company,
    )


@router.get("/metrics", response_model=ErpMetricsResponse)
async def get_erp_metrics(
    current_user: AdminOrManagerDep,
    tenant_id: str | None = Query(default=None),
    company_id: Annotated[UUID | None, Query()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_company_id: str | None = Header(default=None, alias="X-Company-Id"),
) -> ErpMetricsResponse:
    """Return tenant-scoped (and optionally company-scoped) ERP KPI telemetry."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        requested_tenant_id=tenant_id,
        header_tenant_id=x_tenant_id,
    )
    effective_company = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
        header_company_id=x_company_id,
    )
    return await order_service.get_metrics(
        tenant_id=effective_tenant,
        company_id=effective_company,
    )
