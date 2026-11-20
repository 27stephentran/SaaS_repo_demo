"""SQLAlchemy 2.0 ORM entities and Pydantic v2 schemas for ERP & Omnichannel workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_inventory_tenant_sku"),
        CheckConstraint("tenant_id <> ''", name="ck_inventory_tenant_non_empty"),
        CheckConstraint("price_vnd >= 0", name="ck_inventory_price_non_negative"),
        CheckConstraint("stock_quantity >= 0", name="ck_inventory_stock_non_negative"),
        Index("idx_inventory_tenant_sku", "tenant_id", "sku"),
        Index(
            "idx_inventory_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default-tenant", index=True
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Customer(Base):
    """Omnichannel customer identified by Meta Graph API Page-Scoped ID (PSID)."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "meta_psid", name="uq_customers_tenant_meta_psid"),
        CheckConstraint("tenant_id <> ''", name="ck_customers_tenant_non_empty"),
        Index("idx_customers_tenant_psid", "tenant_id", "meta_psid"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default-tenant", index=True
    )
    meta_psid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Employee(Base):
    """Staff member receiving automated Telegram Bot API order fulfillment dispatches."""

    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("tenant_id <> ''", name="ck_employees_tenant_non_empty"),
        Index("idx_employees_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default-tenant", index=True
    )
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
    __table_args__ = (
        UniqueConstraint("tenant_id", "order_code", name="uq_orders_tenant_order_code"),
        CheckConstraint("tenant_id <> ''", name="ck_orders_tenant_non_empty"),
        CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'PAID', 'FULFILLING', 'COMPLETED', 'CANCELLED')",
            name="ck_orders_valid_status",
        ),
        CheckConstraint("total_amount_vnd >= 0", name="ck_orders_total_non_negative"),
        Index("idx_orders_tenant_status", "tenant_id", "status"),
        Index(
            "idx_orders_sepay_transaction_id",
            "sepay_transaction_id",
            unique=True,
            postgresql_where=text("sepay_transaction_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default-tenant", index=True
    )
    order_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class SepayWebhookLog(Base):
    """Deduplication and audit log for inbound SePay bank transaction webhooks."""

    __tablename__ = "sepay_webhook_logs"
    __table_args__ = (
        CheckConstraint("tenant_id <> ''", name="ck_sepay_logs_tenant_non_empty"),
        CheckConstraint("transfer_amount >= 0", name="ck_sepay_logs_amount_non_negative"),
        Index("idx_sepay_logs_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sepay_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default-tenant")
    order_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    transfer_type: Mapped[str] = mapped_column(String(16), nullable=False)
    transfer_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sub_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PROCESSED")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class UserAccount(Base):
    """Tenant-scoped operator/staff account for authenticated API access."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        CheckConstraint("tenant_id <> ''", name="ck_users_tenant_non_empty"),
        Index("idx_users_tenant_email", "tenant_id", "email"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="staff")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ApiToken(Base):
    """Issued token metadata supporting JTI revocation and refresh token rotation."""

    __tablename__ = "api_tokens"
    __table_args__ = (
        CheckConstraint("tenant_id <> ''", name="ck_api_tokens_tenant_non_empty"),
        CheckConstraint("token_type IN ('access', 'refresh')", name="ck_api_tokens_type"),
        Index("idx_api_tokens_tenant_user", "tenant_id", "user_id"),
        Index("idx_api_tokens_token_hash", "token_hash"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    jti: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    token_type: Mapped[str] = mapped_column(String(32), nullable=False, default="refresh")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class TenantChannel(Base):
    """Channel-to-tenant routing entry for Meta Page IDs and SePay bank accounts."""

    __tablename__ = "tenant_channels"
    __table_args__ = (
        UniqueConstraint(
            "channel_type", "external_id", "sub_account", name="uq_tenant_channels_lookup"
        ),
        CheckConstraint("tenant_id <> ''", name="ck_tenant_channels_tenant_non_empty"),
        CheckConstraint(
            "channel_type IN ('meta_page', 'sepay_account')",
            name="ck_tenant_channels_type",
        ),
        Index("idx_tenant_channels_tenant", "tenant_id", "channel_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sub_account: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class WebhookDedupEvent(Base):
    """Multi-provider inbound webhook deduplication ledger."""

    __tablename__ = "webhook_dedup_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "external_event_id",
            name="uq_webhook_dedup_tenant_provider_event",
        ),
        CheckConstraint("tenant_id <> ''", name="ck_webhook_dedup_tenant_non_empty"),
        Index("idx_webhook_dedup_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class OutboxJob(Base):
    """Transactional outbox job for reliable async integration dispatches."""

    __tablename__ = "outbox_jobs"
    __table_args__ = (
        CheckConstraint("tenant_id <> ''", name="ck_outbox_jobs_tenant_non_empty"),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'DEAD_LETTER')",
            name="ck_outbox_jobs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_outbox_jobs_attempts_non_negative"),
        Index("idx_outbox_jobs_status_retry", "status", "next_retry_at"),
        Index("idx_outbox_jobs_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
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

    model_config = ConfigDict(from_attributes=True)

    sku: str
    name: str
    quantity: int = Field(..., ge=1)
    unit_price_vnd: int = Field(..., ge=0)


OrderItemSchema = OrderLineItem


class OrderLineItemRequest(BaseModel):
    """Client order-line request; `unit_price_vnd` and `name` are deprecated/ignored in T1.2."""

    model_config = ConfigDict(from_attributes=True)

    sku: str = Field(..., min_length=1, max_length=64)
    quantity: int = Field(..., ge=1)
    unit_price_vnd: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, max_length=255)


class OrderCreateRequest(BaseModel):
    """Payload to create an order and generate a dynamic SePay VietQR payment link."""

    tenant_id: str = Field(default="default-tenant")
    customer_id: UUID
    assigned_employee_id: UUID | None = None
    items: list[OrderLineItemRequest] = Field(..., min_length=1)


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
    """Inbound SePay Webhook payload (`developer.sepay.vn/vi/sepay-webhooks/tich-hop-webhook`)."""

    id: int | str
    gateway: str | None = "MBBank"
    transactionDate: str | None = ""
    accountNumber: str | None = ""
    subAccount: str | None = None
    code: str | None = None
    content: str | None = ""
    transferType: str = "in"
    description: str | None = ""
    transferAmount: int = Field(..., ge=0)
    referenceCode: str | None = ""
    accumulated: int | None = 0


SepayWebhookPayload = SePayIPNWebhookPayload


class SePayGatewayOrderInfo(BaseModel):
    """Nested `order` block inside SePay Payment Gateway IPN payloads."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    order_id: str | None = None
    order_status: str | None = None
    order_currency: str | None = None
    order_amount: str | int | float | None = None
    order_invoice_number: str | None = None
    order_description: str | None = None
    merchant: str | None = None


class SePayGatewayTransactionInfo(BaseModel):
    """Nested `transaction` block inside SePay Payment Gateway IPN payloads."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    payment_method: str | None = None
    transaction_id: str | None = None
    transaction_type: str | None = None
    transaction_date: str | None = None
    transaction_status: str | None = None
    transaction_amount: str | int | float | None = None
    transaction_currency: str | None = None


class SePayGatewayCustomerInfo(BaseModel):
    """Nested `customer` block inside SePay Payment Gateway IPN payloads."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    customer_id: str | None = None


class SePayGatewayIPNPayload(BaseModel):
    """Inbound SePay Payment Gateway IPN payload (`ORDER_PAID`, `TRANSACTION_VOID`, etc.)."""

    model_config = ConfigDict(extra="allow")

    timestamp: int | str | None = None
    notification_type: str
    order: SePayGatewayOrderInfo | None = None
    transaction: SePayGatewayTransactionInfo | None = None
    customer: SePayGatewayCustomerInfo | None = None


class SePayCheckoutRequest(BaseModel):
    """Optional checkout initiation parameters for SePay Payment Gateway."""

    payment_method: str | None = None
    success_url: str | None = None
    error_url: str | None = None
    cancel_url: str | None = None
    lang: str = "vi"


class SePayCheckoutFormResponse(BaseModel):
    """Server-signed SePay Payment Gateway checkout form payload."""

    checkout_url: str
    fields: dict[str, Any]
    order_id: UUID | None = None
    order_code: str | None = None
    total_amount_vnd: int | None = None


class TokenIssueRequest(BaseModel):
    """Request payload for issuing a tenant-scoped JWT access/refresh token pair."""

    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=256)
    tenant_id: str = Field(default="default-tenant", min_length=1, max_length=64)


class TokenPairResponse(BaseModel):
    """Issued access and refresh token pair metadata."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., ge=1)
    tenant_id: str
    role: str | None = None


class TokenRefreshRequest(BaseModel):
    """Request payload for rotating a refresh token into a fresh token pair."""

    refresh_token: str = Field(..., min_length=1)


class TokenLogoutRequest(BaseModel):
    """Optional request payload to also revoke a refresh token on logout."""

    refresh_token: str | None = Field(default=None)


class AuthenticatedPrincipalResponse(BaseModel):
    """Authenticated principal identity returned by `/api/v1/auth/me`."""

    user_id: str
    email: str
    tenant_id: str
    role: str


class WebhookLogResponse(BaseModel):
    """Serialized SePay webhook audit log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sepay_transaction_id: str
    tenant_id: str
    order_code: str | None = None
    transfer_type: str
    transfer_amount: int
    account_number: str | None = None
    sub_account: str | None = None
    reference_code: str | None = None
    processing_status: str
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ErpMetricsResponse(BaseModel):
    """Tenant-scoped ERP telemetry summary for dashboard KPIs."""

    tenant_id: str
    active_inventory_skus: int = Field(..., ge=0)
    vectorized_skus: int = Field(..., ge=0)
    pending_vietqr_orders: int = Field(..., ge=0)
    paid_and_dispatched_orders: int = Field(..., ge=0)
    total_orders: int = Field(..., ge=0)
    webhook_events_count: int = Field(..., ge=0)
    orders_by_status: dict[str, int] = Field(default_factory=dict)
