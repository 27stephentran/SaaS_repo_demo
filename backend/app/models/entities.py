"""SQLAlchemy 2.0 ORM entities and Pydantic v2 schemas for ERP & Omnichannel workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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


class PermissionGroup(StrEnum):
    """Isolated API permission groups separating live tenant APIs from demo sandbox APIs."""

    LIVE_API = "live_api"
    DEMO_API = "demo_api"


# ==============================================================================
# SQLAlchemy 2.0 ORM Models (Supabase PostgreSQL + pgvector)
# ==============================================================================


class Company(Base):
    """Tenant-scoped subsidiary/branch company with dedicated bank & SePay routing."""

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_companies_tenant_code"),
        CheckConstraint("tenant_id <> ''", name="ck_companies_tenant_non_empty"),
        Index("idx_companies_tenant_active", "tenant_id", "is_active"),
        Index("idx_companies_tenant_code", "tenant_id", "code"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    bank_code: Mapped[str] = mapped_column(String(32), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    bank_account_number: Mapped[str] = mapped_column(String(64), nullable=False)
    bank_account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sepay_sub_account: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    sepay_memo_prefix: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sepay_bank_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sepay_bankhub_company_xid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class InventoryItem(Base):
    """Product catalog item with 768-dim `text-embedding-004` pgvector column."""

    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_inventory_tenant_sku"),
        CheckConstraint("tenant_id <> ''", name="ck_inventory_tenant_non_empty"),
        CheckConstraint("price_vnd >= 0", name="ck_inventory_price_non_negative"),
        CheckConstraint("stock_quantity >= 0", name="ck_inventory_stock_non_negative"),
        Index("idx_inventory_tenant_sku", "tenant_id", "sku"),
        Index("idx_inventory_tenant_active", "tenant_id", "is_active", "deactivated_at"),
        Index("idx_inventory_tenant_company_active", "tenant_id", "company_id", "is_active"),
        Index(
            "idx_inventory_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="Hardware")
    price_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
        Index("idx_customers_tenant_active", "tenant_id", "is_active", "deactivated_at"),
        Index("idx_customers_tenant_company_active", "tenant_id", "company_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    meta_psid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Employee(Base):
    """Staff member receiving automated Telegram Bot API order fulfillment dispatches."""

    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("tenant_id <> ''", name="ck_employees_tenant_non_empty"),
        Index("idx_employees_tenant_active", "tenant_id", "is_active"),
        Index("idx_employees_tenant_company_active", "tenant_id", "company_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="fulfillment_specialist")
    telegram_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
        Index("idx_orders_tenant_active", "tenant_id", "is_active", "deactivated_at"),
        Index("idx_orders_tenant_company_status", "tenant_id", "company_id", "status"),
        Index(
            "idx_orders_sepay_transaction_id",
            "sepay_transaction_id",
            unique=True,
            postgresql_where=text("sepay_transaction_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
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
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
        Index("idx_sepay_logs_tenant_company_created", "tenant_id", "company_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sepay_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
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
    """Tenant-scoped operator/manager/staff account for authenticated API access."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        CheckConstraint("tenant_id <> ''", name="ck_users_tenant_non_empty"),
        CheckConstraint("role IN ('admin', 'manager', 'staff', 'demo')", name="ck_users_role_valid"),
        CheckConstraint(
            "role IN ('admin', 'demo') OR company_id IS NOT NULL",
            name="ck_users_company_required_for_non_admin",
        ),
        Index("idx_users_tenant_email", "tenant_id", "email"),
        Index("idx_users_tenant_company", "tenant_id", "company_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="staff")
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    employee_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    """Channel-to-tenant and company routing entry for Meta Page IDs and SePay bank accounts."""

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
        Index("idx_tenant_channels_tenant_company", "tenant_id", "company_id", "channel_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sub_account: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ChannelConversation(Base):
    """Persistent omnichannel customer chat thread across Messenger, Instagram, Telegram, and Web."""

    __tablename__ = "channel_conversations"
    __table_args__ = (
        CheckConstraint("tenant_id <> ''", name="ck_channel_conversations_tenant_non_empty"),
        CheckConstraint(
            "channel IN ('messenger', 'instagram', 'telegram', 'web', 'other')",
            name="ck_channel_conversations_channel",
        ),
        CheckConstraint(
            "unread_count >= 0",
            name="ck_channel_conversations_unread_non_negative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Customer")
    last_message_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ChannelMessage(Base):
    """Individual inbound or outbound message inside an omnichannel conversation."""

    __tablename__ = "channel_messages"
    __table_args__ = (
        CheckConstraint("tenant_id <> ''", name="ck_channel_messages_tenant_non_empty"),
        CheckConstraint(
            "channel IN ('messenger', 'instagram', 'telegram', 'web', 'other')",
            name="ck_channel_messages_channel",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_channel_messages_direction",
        ),
        CheckConstraint(
            "sender_type IN ('customer', 'ai_bot', 'manager', 'admin')",
            name="ck_channel_messages_sender_type",
        ),
        Index("idx_channel_messages_conv_created", "conversation_id", "created_at"),
        Index(
            "idx_channel_messages_tenant_company_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("channel_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    order_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
# Pydantic v2 Strict Request / Response Serializers
# ==============================================================================


class InventoryCreateRequest(BaseModel):
    """Payload to create a new inventory item and compute its 768-dim Gemini embedding."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    company_id: UUID | None = None
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
    company_id: UUID | None = None
    sku: str
    name: str
    description: str
    category: str = "Hardware"
    price_vnd: int
    stock_quantity: int
    has_embedding: bool = True
    is_active: bool = True
    deactivated_at: datetime | None = None
    similarity_distance: float | None = None
    similarity_score: float | None = None
    created_at: datetime
    updated_at: datetime


class InventorySearchRequest(BaseModel):
    """Semantic vector search request over `inventory` descriptions."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    company_id: UUID | None = None
    limit: int = Field(default=5, ge=1, le=25)
    include_inactive: bool = False


class OrderLineItem(BaseModel):
    """Individual line item inside an order."""

    model_config = ConfigDict(from_attributes=True)

    sku: str
    name: str
    quantity: int = Field(..., ge=1)
    unit_price_vnd: int = Field(..., ge=0)


OrderItemSchema = OrderLineItem


class OrderLineItemRequest(BaseModel):
    """Client order-line request; `unit_price_vnd` and `name` are ignored for server-authoritative pricing."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    sku: str = Field(..., min_length=1, max_length=64)
    quantity: int = Field(..., ge=1)
    unit_price_vnd: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, max_length=255)


class OrderCreateRequest(BaseModel):
    """Payload to create an order and generate a dynamic SePay VietQR payment link."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    company_id: UUID | None = None
    customer_id: UUID
    assigned_employee_id: UUID | None = None
    items: list[OrderLineItemRequest] = Field(..., min_length=1)


class OrderStatusUpdateRequest(BaseModel):
    """Payload to transition an order status from the Nuxt 3 ERP workspace."""

    model_config = ConfigDict(extra="forbid")

    status: OrderStatus

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status_alias(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.lower() == "in_progress":
                return OrderStatus.FULFILLING
            return cleaned.upper()
        return value


class OrderResponse(BaseModel):
    """Serialized ERP order with SePay VietQR metadata and Nuxt 3 frontend 1:1 compatibility."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    company_id: UUID | None = None
    company_name: str = ""
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
    is_active: bool = True
    deactivated_at: datetime | None = None
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
    company_id: UUID | None = None
    meta_psid: str
    full_name: str
    phone: str | None = None
    is_active: bool = True
    deactivated_at: datetime | None = None
    created_at: datetime


class EmployeeResponse(BaseModel):
    """Serialized staff member with `telegram_user_id`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    company_id: UUID | None = None
    full_name: str
    role: str
    telegram_user_id: str
    is_active: bool
    deactivated_at: datetime | None = None
    created_at: datetime


class SoftDeleteResponse(BaseModel):
    """Serialized soft-delete inactivation outcome."""

    resource: str
    record_id: UUID
    tenant_id: str
    is_active: bool = False
    deactivated_at: datetime


class RestoreResponse(BaseModel):
    """Serialized restore outcome for an archived record."""

    resource: str
    record_id: UUID
    tenant_id: str
    is_active: bool = True
    deactivated_at: datetime | None = None


class RetentionCleanupRequest(BaseModel):
    """Payload to run or preview expired soft-deleted record retention cleanup."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True
    retention_days_override: int | None = Field(default=None, ge=1, le=3650)


class RetentionCleanupResponse(BaseModel):
    """Serialized summary of retention cleanup eligibility, backup hook, and deletion counts."""

    tenant_id: str
    dry_run: bool
    retention_days: int = Field(..., ge=1)
    cutoff_at: datetime
    backup_provider: str = "deferred"
    backup_status: str
    eligible_counts: dict[str, int] = Field(default_factory=dict)
    deleted_counts: dict[str, int] = Field(default_factory=dict)
    skipped_fk_protected_counts: dict[str, int] = Field(default_factory=dict)


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

    model_config = ConfigDict(extra="forbid")

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


class TelegramDispatchSummary(BaseModel):
    """Serialized Telegram staff fulfillment alert summary."""

    dispatched: bool
    queued: bool = False
    simulated: bool = False
    idempotent_duplicate: bool = False
    telegram_user_id: str = ""
    recipient_count: int = Field(default=1, ge=0)
    order_code: str | None = None
    outbox_job_ids: list[str] = Field(default_factory=list)
    preview_text: str | None = None
    error: str | None = None


class SePayWebhookResponse(BaseModel):
    """Canonical serialized response for SePay Bank Transfer and Payment Gateway IPN webhooks."""

    success: bool = True
    idempotent_duplicate: bool = False
    ignored: bool = False
    payment_verified: bool | None = None
    reason: str | None = None
    notification_type: str | None = None
    order_code: str | None = None
    order_status: str | None = None
    expected_amount_vnd: int | None = None
    received_amount_vnd: int | None = None
    sepay_transaction_id: str
    telegram_dispatch: TelegramDispatchSummary | None = None


class MetaDeliveryStatus(BaseModel):
    """Serialized Meta Graph API delivery outcome for a webhook reply."""

    delivered: bool = True
    simulated: bool = False
    queued: bool = False
    outbox_job_id: str | None = None
    api_version: str | None = None
    recipient_id: str | None = None
    comment_id: str | None = None
    message_text: str | None = None
    message_id: str | None = None
    error: str | None = None
    error_type: str | None = None
    status_code: int | None = None


class MetaWebhookEventResult(BaseModel):
    """Serialized outcome of a single Meta Messenger/Comment webhook event."""

    type: str
    tenant_id: str | None = None
    page_id: str | None = None
    status: str | None = None
    mid: str | None = None
    sender_psid: str | None = None
    comment_id: str | None = None
    reply_text: str | None = None
    model_used: str | None = None
    order_intent_detected: bool | None = None
    order_code: str | None = None
    sepay_qr_url: str | None = None
    outbox_job_id: str | None = None
    meta_delivery: MetaDeliveryStatus | None = None


class MetaWebhookResponse(BaseModel):
    """Serialized response returned by `POST /api/v1/webhooks/meta`."""

    status: str = "EVENT_RECEIVED"
    processed_count: int = Field(..., ge=0)
    events: list[MetaWebhookEventResult] = Field(default_factory=list)


class TokenIssueRequest(BaseModel):
    """Request payload for issuing a tenant-scoped `live_api` JWT access/refresh token pair."""

    model_config = ConfigDict(extra="forbid")

    identifier: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=256)
    tenant_id: str = Field(..., min_length=1, max_length=64)

    @model_validator(mode="after")
    def _validate_identifier_or_email(self) -> TokenIssueRequest:
        ident = (self.identifier or "").strip()
        mail = (self.email or "").strip()
        if not ident and not mail:
            raise ValueError("Either identifier or email must be provided")
        return self

    @property
    def resolved_identifier(self) -> str:
        """Return the trimmed login identifier (`identifier` preferred over `email`)."""
        return (self.identifier or self.email or "").strip()


class SignupRequest(BaseModel):
    """Request payload for self-serve tenant-scoped operator signup."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=256)
    tenant_id: str = Field(..., min_length=1, max_length=64)
    full_name: str = Field(default="", max_length=255)


class ChangePasswordRequest(BaseModel):
    """Strict request payload for forced or voluntary password rotation."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


class TokenPairResponse(BaseModel):
    """Issued access and refresh token pair metadata."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., ge=1)
    tenant_id: str
    role: str | None = None
    permission_group: PermissionGroup = PermissionGroup.LIVE_API
    user_id: str | None = None
    username: str | None = None
    full_name: str = ""
    company_id: UUID | None = None
    employee_id: UUID | None = None
    must_change_password: bool = False


class TokenRefreshRequest(BaseModel):
    """Request payload for rotating a refresh token into a fresh token pair."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(..., min_length=1)


class TokenLogoutRequest(BaseModel):
    """Optional request payload to also revoke a refresh token on logout."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None)


class LogoutResponse(BaseModel):
    """Serialized logout response confirming token JTI revocation."""

    status: Literal["logged_out"] = "logged_out"
    revoked: bool = True


class AuthenticatedPrincipalResponse(BaseModel):
    """Authenticated principal identity returned by `/api/v1/auth/me`."""

    user_id: str
    email: str
    tenant_id: str
    role: str
    permission_group: PermissionGroup
    username: str | None = None
    full_name: str = ""
    company_id: UUID | None = None
    employee_id: UUID | None = None
    must_change_password: bool = False


class BankDirectoryEntry(BaseModel):
    """Curated Vietnamese bank directory entry for VietQR and SePay setup."""

    code: str
    bin: str = ""
    name: str
    short_name: str
    vietqr_supported: bool = True
    sepay_supported: bool = True


class SePayLinkedBankAccount(BaseModel):
    """Bank account synced from SePay User API v2."""

    id: str
    bank_code: str = ""
    bank_short_name: str = ""
    account_number: str
    account_holder_name: str = ""
    accumulated: int | None = None
    active: bool = True


class BankCatalogResponse(BaseModel):
    """Combined Vietnam bank directory and SePay-linked bank accounts response."""

    banks: list[BankDirectoryEntry] = Field(default_factory=list)
    sepay_linked_accounts: list[SePayLinkedBankAccount] = Field(default_factory=list)
    sepay_api_configured: bool = False
    bankhub_configured: bool = False


class CompanyCreateRequest(BaseModel):
    """Strict payload for creating a tenant-scoped subsidiary/branch company."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=255)
    code: str = Field(..., min_length=2, max_length=32)
    bank_code: str = Field(..., min_length=2, max_length=32)
    bank_name: str = Field(default="", max_length=128)
    bank_account_number: str = Field(..., min_length=4, max_length=64)
    bank_account_name: str = Field(..., min_length=2, max_length=255)
    sepay_sub_account: str = Field(default="", max_length=64)
    sepay_memo_prefix: str = Field(default="", max_length=32)
    sepay_bank_account_id: str | None = Field(default=None, max_length=128)
    meta_page_id: str | None = Field(default=None, max_length=128)


class CompanyBankUpdateRequest(BaseModel):
    """Strict payload for updating a company's SePay banking and VietQR configuration."""

    model_config = ConfigDict(extra="forbid")

    bank_code: str = Field(..., min_length=2, max_length=32)
    bank_name: str = Field(default="", max_length=128)
    bank_account_number: str = Field(..., min_length=4, max_length=64)
    bank_account_name: str = Field(..., min_length=2, max_length=255)
    sepay_sub_account: str = Field(default="", max_length=64)
    sepay_memo_prefix: str = Field(default="", max_length=32)
    sepay_bank_account_id: str | None = Field(default=None, max_length=128)


class CompanyResponse(BaseModel):
    """Serialized representation of a company."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    name: str
    code: str
    bank_code: str
    bank_name: str = ""
    bank_account_number: str
    bank_account_name: str
    sepay_sub_account: str = ""
    sepay_memo_prefix: str = ""
    sepay_bank_account_id: str | None = None
    sepay_bankhub_company_xid: str | None = None
    is_active: bool = True
    deactivated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BankHubLinkTokenRequest(BaseModel):
    """Strict optional parameters for generating a SePay Bank Hub link token."""

    model_config = ConfigDict(extra="forbid")

    bank_code: str | None = Field(default=None, max_length=32)
    redirect_url: str | None = Field(default=None, max_length=500)


class BankHubLinkTokenResponse(BaseModel):
    """Response containing SePay Bank Hub link status and optional link token."""

    company_id: UUID
    configured: bool
    sepay_bankhub_company_xid: str | None = None
    link_token: str | None = None
    connect_url: str | None = None
    expires_at: str | None = None
    detail: str = ""


class ManagerCreateRequest(BaseModel):
    """Strict payload for an Admin to provision a company Manager account."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=2, max_length=255)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    username: str | None = Field(default=None, min_length=2, max_length=64)
    password: str | None = Field(default=None, min_length=8, max_length=256)


class ManagerSummaryResponse(BaseModel):
    """Serialized company Manager account summary."""

    user_id: UUID
    tenant_id: str
    company_id: UUID
    company_name: str = ""
    email: str
    username: str | None = None
    full_name: str = ""
    must_change_password: bool = False
    is_active: bool = True
    created_at: datetime


class ManagerCreateResponse(BaseModel):
    """Response returned once upon Manager provisioning with temporary credentials."""

    user_id: UUID
    tenant_id: str
    company_id: UUID
    company_name: str = ""
    email: str
    username: str
    full_name: str
    role: str = "manager"
    temporary_password: str
    must_change_password: bool = True
    created_at: datetime


class StaffCreateRequest(BaseModel):
    """Strict payload for a Manager to provision a company Staff member + login account."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=2, max_length=255)
    role: str = Field(default="fulfillment_specialist", min_length=2, max_length=64)
    telegram_user_id: str = Field(default="", max_length=64)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    username: str | None = Field(default=None, min_length=2, max_length=64)
    password: str | None = Field(default=None, min_length=8, max_length=256)


class StaffMemberSummaryResponse(BaseModel):
    """Serialized staff member with linked user account & order workload metrics."""

    employee_id: UUID
    user_id: UUID | None = None
    tenant_id: str
    company_id: UUID | None = None
    full_name: str
    role: str
    telegram_user_id: str = ""
    email: str | None = None
    username: str | None = None
    must_change_password: bool = False
    is_active: bool = True
    active_orders_count: int = Field(default=0, ge=0)
    completed_orders_count: int = Field(default=0, ge=0)
    created_at: datetime


class StaffCreateResponse(BaseModel):
    """Response returned once upon Staff provisioning with temporary credentials."""

    employee_id: UUID
    user_id: UUID
    tenant_id: str
    company_id: UUID
    full_name: str
    role: str
    telegram_user_id: str = ""
    email: str
    username: str
    temporary_password: str
    must_change_password: bool = True
    created_at: datetime


class DailyIncomePoint(BaseModel):
    """Single day revenue & order count bucket for Manager dashboard analytics."""

    date: str
    revenue_vnd: int = Field(default=0, ge=0)
    orders_count: int = Field(default=0, ge=0)


class ManagerDashboardResponse(BaseModel):
    """Company-scoped analytics dashboard for Manager and Admin views."""

    tenant_id: str
    company_id: UUID | None = None
    company_name: str = ""
    total_revenue_vnd: int = Field(default=0, ge=0)
    paid_orders_count: int = Field(default=0, ge=0)
    pending_orders_count: int = Field(default=0, ge=0)
    total_orders_count: int = Field(default=0, ge=0)
    active_customers_count: int = Field(default=0, ge=0)
    active_staff_count: int = Field(default=0, ge=0)
    open_chats_count: int = Field(default=0, ge=0)
    daily_income: list[DailyIncomePoint] = Field(default_factory=list)
    staff_workload: list[StaffMemberSummaryResponse] = Field(default_factory=list)
    recent_customers: list[CustomerResponse] = Field(default_factory=list)


class ChannelConversationResponse(BaseModel):
    """Serialized omnichannel customer chat conversation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    company_id: UUID | None = None
    customer_id: UUID | None = None
    channel: str
    external_thread_id: str
    customer_name: str = "Customer"
    last_message_preview: str = ""
    last_message_at: datetime
    unread_count: int = Field(default=0, ge=0)
    status: str = "open"
    created_at: datetime
    updated_at: datetime


class ChannelMessageResponse(BaseModel):
    """Serialized message inside an omnichannel conversation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    company_id: UUID | None = None
    conversation_id: UUID
    channel: str
    external_message_id: str | None = None
    direction: str
    sender_type: str
    sender_name: str = ""
    content: str
    order_code: str | None = None
    created_at: datetime


class ChatReplyRequest(BaseModel):
    """Strict payload for Manager/Admin reply to an omnichannel conversation."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=4000)


class DemoTelegramNotifyRequest(BaseModel):
    """Strict request serializer for triggering a Telegram order alert from `/demo`."""

    model_config = ConfigDict(extra="forbid")

    order_code: str = Field(..., min_length=2, max_length=64)
    total_amount_vnd: int = Field(..., ge=0)
    items_summary: str = Field(..., min_length=1, max_length=500)
    customer_name: str | None = Field(default=None, max_length=255)
    telegram_user_id: str | None = Field(default=None, max_length=64)
    sepay_reference: str | None = Field(default=None, max_length=128)


class OrderTelegramNotifyRequest(BaseModel):
    """Optional override target for dispatching a Telegram alert on a live order."""

    model_config = ConfigDict(extra="forbid")

    telegram_user_id: str | None = Field(default=None, max_length=64)


class TelegramDispatchResponse(BaseModel):
    """Serialized response for live and demo Telegram notification dispatches."""

    dispatched: bool
    simulated: bool
    telegram_user_id: str
    order_code: str
    preview_text: str | None = None
    error: str | None = None
    status_code: int | None = None


class HealthCheckResponse(BaseModel):
    """Minimal liveness probe serializer without leaking internal stack metadata."""

    status: Literal["ok", "unhealthy"]


class SystemStackMetadata(BaseModel):
    """Authenticated diagnostic stack metadata."""

    database: str
    ai_sdk: str
    chat_model: str
    pro_model: str
    embedding_model: str
    meta_api_version: str
    payment_gateway: str
    staff_dispatch: str


class SystemHealthResponse(BaseModel):
    """Authenticated `live_api` system health and stack readiness response."""

    status: Literal["ok", "unhealthy"]
    database: Literal["postgres", "fallback", "unavailable"]
    service: str
    version: str
    stack: SystemStackMetadata


class WebhookLogResponse(BaseModel):
    """Serialized SePay webhook audit log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sepay_transaction_id: str
    tenant_id: str
    company_id: UUID | None = None
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
    avg_rag_latency_ms: float | None = None
    orders_by_status: dict[str, int] = Field(default_factory=dict)
