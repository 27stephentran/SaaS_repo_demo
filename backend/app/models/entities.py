"""SQLAlchemy 2.0 ORM entities and Pydantic v2 schemas for ERP & Omnichannel workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for Supabase PostgreSQL + pgvector ORM models."""


class OrderStatus(StrEnum):
    """Canonical lifecycle statuses for an omnichannel B2B/D2C ERP order."""

    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    FULFILLING = "FULFILLING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# ==============================================================================
# SQLAlchemy 2.0 ORM Models (Supabase PostgreSQL + pgvector)
# ==============================================================================


class InventoryItem(Base):
    """Product catalog item with 768-dim `text-embedding-004` pgvector column."""

    __tablename__ = "inventory"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default-tenant", index=True)
    sku: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="Hardware")
    price_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Customer(Base):
    """Omnichannel customer identified by Meta Graph API Page-Scoped ID (PSID)."""

    __tablename__ = "customers"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default-tenant", index=True)
    meta_psid: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Employee(Base):
    """Staff member receiving automated Telegram Bot API order fulfillment dispatches."""

    __tablename__ = "employees"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default-tenant", index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="fulfillment_specialist")
    telegram_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Order(Base):
    """Customer order linking inventory items, SePay VietQR payment state, and staff dispatch."""

    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default-tenant", index=True)
    order_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_employee_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OrderStatus.PENDING_PAYMENT.value, index=True
    )
    total_amount_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sepay_qr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sepay_transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


# ==============================================================================
# Pydantic v2 Request / Response Schemas
# ==============================================================================


class InventoryCreateRequest(BaseModel):
    """Payload to create a new inventory item and compute its 768-dim Gemini embedding."""

    tenant_id: str = Field(default="default-tenant")
    sku: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=2, max_length=255)
    description: str = Field(..., min_length=3)
    category: str = Field(default="Hardware", max_length=128)
    price_vnd: int = Field(..., ge=0)
    stock_quantity: int = Field(default=0, ge=0)


InventoryCreate = InventoryCreateRequest


class InventoryResponse(BaseModel):
    """Serialized representation of an inventory item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    sku: str
    name: str
    description: str
    category: str = "Hardware"
    price_vnd: int
    stock_quantity: int
    has_embedding: bool = True
    similarity_distance: float | None = None
    similarity_score: float | None = None
    created_at: datetime
    updated_at: datetime


class InventorySearchRequest(BaseModel):
    """Semantic vector search request over `inventory` descriptions."""

    query: str = Field(..., min_length=1)
    tenant_id: str = Field(default="default-tenant")
    limit: int = Field(default=5, ge=1, le=25)


class OrderLineItem(BaseModel):
    """Individual line item inside an order."""

    sku: str
    name: str
    quantity: int = Field(..., ge=1)
    unit_price_vnd: int = Field(..., ge=0)


OrderItemSchema = OrderLineItem


class OrderCreateRequest(BaseModel):
    """Payload to create an order and generate a dynamic SePay VietQR payment link."""

    tenant_id: str = Field(default="default-tenant")
    customer_id: UUID
    assigned_employee_id: UUID | None = None
    items: list[OrderLineItem] = Field(..., min_length=1)


class OrderStatusUpdateRequest(BaseModel):
    """Payload to transition an order status from the Nuxt 3 ERP Kanban board."""

    status: OrderStatus


class OrderResponse(BaseModel):
    """Serialized ERP order with SePay VietQR metadata and Nuxt 3 frontend 1:1 compatibility."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    order_code: str
    customer_id: UUID
    customer_name: str = "Enterprise Customer"
    assigned_employee_id: UUID | None = None
    status: OrderStatus
    total_amount_vnd: int
    sepay_qr_url: str | None = None
    vietqr_url: str | None = None
    sepay_transaction_id: str | None = None
    sepay_reference: str = ""
    items_json: list[dict[str, Any]] = Field(default_factory=list)
    items: list[OrderLineItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _sync_frontend_fields(self) -> OrderResponse:
        if not self.items and self.items_json:
            self.items = [OrderLineItem.model_validate(item) for item in self.items_json]
        elif self.items and not self.items_json:
            self.items_json = [item.model_dump() for item in self.items]

        if self.vietqr_url is None and self.sepay_qr_url is not None:
            self.vietqr_url = self.sepay_qr_url
        elif self.sepay_qr_url is None and self.vietqr_url is not None:
            self.sepay_qr_url = self.vietqr_url

        if not self.sepay_reference:
            self.sepay_reference = self.sepay_transaction_id or self.order_code
        return self


class CustomerResponse(BaseModel):
    """Serialized omnichannel customer record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    meta_psid: str
    full_name: str
    phone: str | None = None
    created_at: datetime


class EmployeeResponse(BaseModel):
    """Serialized staff member with `telegram_user_id`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    full_name: str
    role: str
    telegram_user_id: str
    is_active: bool
    created_at: datetime


class SePayIPNWebhookPayload(BaseModel):
    """Inbound SePay IPN webhook payload when a bank transfer occurs."""

    id: int | str
    gateway: str = "MBBank"
    transactionDate: str = ""
    accountNumber: str = ""
    subAccount: str | None = None
    code: str | None = None
    content: str
    transferType: str = "in"
    description: str = ""
    transferAmount: int = Field(..., ge=0)
    referenceCode: str = ""
    accumulated: int = 0
