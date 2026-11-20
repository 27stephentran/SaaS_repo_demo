"""0001 initial multi-tenant schema, pgvector cosine HNSW, and RLS policies.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-20 00:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_RLS_TABLES: tuple[str, ...] = (
    "inventory",
    "customers",
    "employees",
    "orders",
    "sepay_webhook_logs",
    "users",
    "api_tokens",
    "webhook_dedup_events",
    "outbox_jobs",
)


def upgrade() -> None:
    """Create initial multi-tenant schema, cosine pgvector HNSW index, functions, and RLS."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE SEQUENCE IF NOT EXISTS order_code_seq START WITH 1001 INCREMENT BY 1;")

    op.create_table(
        "inventory",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default-tenant"),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False, server_default="Hardware"),
        sa.Column("price_vnd", sa.BigInteger(), nullable=False),
        sa.Column("stock_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_inventory_tenant_non_empty"),
        sa.CheckConstraint("price_vnd >= 0", name="ck_inventory_price_non_negative"),
        sa.CheckConstraint("stock_quantity >= 0", name="ck_inventory_stock_non_negative"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_inventory_tenant_sku"),
    )
    op.create_index("idx_inventory_tenant_sku", "inventory", ["tenant_id", "sku"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_inventory_embedding_hnsw "
        "ON inventory USING hnsw (embedding vector_cosine_ops);"
    )

    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default-tenant"),
        sa.Column("meta_psid", sa.String(length=128), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_customers_tenant_non_empty"),
        sa.UniqueConstraint("tenant_id", "meta_psid", name="uq_customers_tenant_meta_psid"),
    )
    op.create_index("idx_customers_tenant_psid", "customers", ["tenant_id", "meta_psid"])

    op.create_table(
        "employees",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default-tenant"),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="fulfillment_specialist"),
        sa.Column("telegram_user_id", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_employees_tenant_non_empty"),
    )
    op.create_index("idx_employees_tenant_active", "employees", ["tenant_id", "is_active"])

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default-tenant"),
        sa.Column("order_code", sa.String(length=64), nullable=False),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assigned_employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING_PAYMENT"),
        sa.Column("total_amount_vnd", sa.BigInteger(), nullable=False),
        sa.Column("sepay_qr_url", sa.Text(), nullable=True),
        sa.Column("sepay_transaction_id", sa.String(length=128), nullable=True),
        sa.Column(
            "items_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_orders_tenant_non_empty"),
        sa.CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'PAID', 'FULFILLING', 'COMPLETED', 'CANCELLED')",
            name="ck_orders_valid_status",
        ),
        sa.CheckConstraint("total_amount_vnd >= 0", name="ck_orders_total_non_negative"),
        sa.UniqueConstraint("tenant_id", "order_code", name="uq_orders_tenant_order_code"),
    )
    op.create_index("idx_orders_tenant_status", "orders", ["tenant_id", "status"])
    op.create_index("idx_orders_order_code", "orders", ["order_code"])
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_sepay_transaction_id "
        "ON orders (sepay_transaction_id) WHERE sepay_transaction_id IS NOT NULL;"
    )

    op.create_table(
        "sepay_webhook_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("sepay_transaction_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default-tenant"),
        sa.Column("order_code", sa.String(length=64), nullable=True),
        sa.Column("transfer_type", sa.String(length=16), nullable=False),
        sa.Column("transfer_amount", sa.BigInteger(), nullable=False),
        sa.Column("account_number", sa.String(length=64), nullable=True),
        sa.Column("sub_account", sa.String(length=64), nullable=True),
        sa.Column("reference_code", sa.String(length=128), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False, server_default="PROCESSED"),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_sepay_logs_tenant_non_empty"),
        sa.CheckConstraint("transfer_amount >= 0", name="ck_sepay_logs_amount_non_negative"),
    )
    op.create_index("idx_sepay_logs_tenant_created", "sepay_webhook_logs", ["tenant_id", "created_at"])
    op.create_index("idx_sepay_logs_order_code", "sepay_webhook_logs", ["order_code"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="staff"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_users_tenant_non_empty"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )
    op.create_index("idx_users_tenant_email", "users", ["tenant_id", "email"])

    op.create_table(
        "api_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("jti", sa.String(length=128), nullable=False, unique=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("token_type", sa.String(length=32), nullable=False, server_default="refresh"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_api_tokens_tenant_non_empty"),
        sa.CheckConstraint("token_type IN ('access', 'refresh')", name="ck_api_tokens_type"),
    )
    op.create_index("idx_api_tokens_tenant_user", "api_tokens", ["tenant_id", "user_id"])
    op.create_index("idx_api_tokens_token_hash", "api_tokens", ["token_hash"])

    op.create_table(
        "tenant_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("sub_account", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_tenant_channels_tenant_non_empty"),
        sa.CheckConstraint("channel_type IN ('meta_page', 'sepay_account')", name="ck_tenant_channels_type"),
        sa.UniqueConstraint("channel_type", "external_id", "sub_account", name="uq_tenant_channels_lookup"),
    )
    op.create_index("idx_tenant_channels_tenant", "tenant_channels", ["tenant_id", "channel_type"])

    op.create_table(
        "webhook_dedup_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_event_id", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_webhook_dedup_tenant_non_empty"),
        sa.UniqueConstraint("tenant_id", "provider", "external_event_id", name="uq_webhook_dedup_tenant_provider_event"),
    )
    op.create_index("idx_webhook_dedup_tenant_created", "webhook_dedup_events", ["tenant_id", "created_at"])

    op.create_table(
        "outbox_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("tenant_id <> ''", name="ck_outbox_jobs_tenant_non_empty"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'DEAD_LETTER')",
            name="ck_outbox_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_jobs_attempts_non_negative"),
    )
    op.create_index("idx_outbox_jobs_status_retry", "outbox_jobs", ["status", "next_retry_at"])
    op.create_index("idx_outbox_jobs_tenant_status", "outbox_jobs", ["tenant_id", "status"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION match_inventory_embeddings(
            query_embedding vector(768),
            match_tenant_id VARCHAR(64) DEFAULT 'default-tenant',
            match_limit INTEGER DEFAULT 5
        )
        RETURNS TABLE (
            id UUID,
            sku VARCHAR(64),
            name VARCHAR(255),
            description TEXT,
            category VARCHAR(128),
            price_vnd BIGINT,
            stock_quantity INTEGER,
            distance DOUBLE PRECISION,
            similarity_score DOUBLE PRECISION
        )
        LANGUAGE sql STABLE
        AS $$
            SELECT
                i.id,
                i.sku,
                i.name,
                i.description,
                i.category,
                i.price_vnd,
                i.stock_quantity,
                (i.embedding <=> query_embedding) AS distance,
                (1.0 - (i.embedding <=> query_embedding)) AS similarity_score
            FROM inventory i
            WHERE i.tenant_id = match_tenant_id
              AND i.embedding IS NOT NULL
            ORDER BY i.embedding <=> query_embedding ASC
            LIMIT match_limit;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION resolve_tenant_by_channel(
            p_channel_type TEXT,
            p_external_id TEXT,
            p_sub_account TEXT DEFAULT ''
        )
        RETURNS VARCHAR(64)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT tc.tenant_id
            FROM tenant_channels tc
            WHERE tc.channel_type = p_channel_type
              AND tc.external_id = p_external_id
              AND tc.sub_account = COALESCE(p_sub_account, '')
              AND tc.is_active = TRUE
            LIMIT 1;
        $$;
        """
    )

    for table_name in TENANT_RLS_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table_name} ON {table_name}
                USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));
            """
        )

    # Non-owner app role setup (provision outside migration with least privilege):
    # CREATE ROLE app_runtime NOINHERIT LOGIN PASSWORD '<strong-secret>'
    #     NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    # GRANT USAGE ON SCHEMA public TO app_runtime;
    # GRANT SELECT, INSERT, UPDATE, DELETE ON
    #     inventory, customers, employees, orders, sepay_webhook_logs,
    #     users, api_tokens, webhook_dedup_events, outbox_jobs TO app_runtime;
    # GRANT USAGE, SELECT ON SEQUENCE
    #     order_code_seq, sepay_webhook_logs_id_seq, webhook_dedup_events_id_seq TO app_runtime;
    # REVOKE ALL ON tenant_channels FROM PUBLIC;
    # GRANT EXECUTE ON FUNCTION
    #     match_inventory_embeddings(vector, varchar, integer),
    #     resolve_tenant_by_channel(text, text, text) TO app_runtime;


def downgrade() -> None:
    """Revert RLS policies, functions, tables, and sequences cleanly."""
    for table_name in reversed(TENANT_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name};")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP FUNCTION IF EXISTS resolve_tenant_by_channel(TEXT, TEXT, TEXT);")
    op.execute("DROP FUNCTION IF EXISTS match_inventory_embeddings(vector, VARCHAR, INTEGER);")

    op.drop_index("idx_outbox_jobs_tenant_status", table_name="outbox_jobs")
    op.drop_index("idx_outbox_jobs_status_retry", table_name="outbox_jobs")
    op.drop_table("outbox_jobs")

    op.drop_index("idx_webhook_dedup_tenant_created", table_name="webhook_dedup_events")
    op.drop_table("webhook_dedup_events")

    op.drop_index("idx_tenant_channels_tenant", table_name="tenant_channels")
    op.drop_table("tenant_channels")

    op.drop_index("idx_api_tokens_token_hash", table_name="api_tokens")
    op.drop_index("idx_api_tokens_tenant_user", table_name="api_tokens")
    op.drop_table("api_tokens")

    op.drop_index("idx_users_tenant_email", table_name="users")
    op.drop_table("users")

    op.drop_index("idx_sepay_logs_order_code", table_name="sepay_webhook_logs")
    op.drop_index("idx_sepay_logs_tenant_created", table_name="sepay_webhook_logs")
    op.drop_table("sepay_webhook_logs")

    op.execute("DROP INDEX IF EXISTS idx_orders_sepay_transaction_id;")
    op.drop_index("idx_orders_order_code", table_name="orders")
    op.drop_index("idx_orders_tenant_status", table_name="orders")
    op.drop_table("orders")

    op.drop_index("idx_employees_tenant_active", table_name="employees")
    op.drop_table("employees")

    op.drop_index("idx_customers_tenant_psid", table_name="customers")
    op.drop_table("customers")

    op.execute("DROP INDEX IF EXISTS idx_inventory_embedding_hnsw;")
    op.drop_index("idx_inventory_tenant_sku", table_name="inventory")
    op.drop_table("inventory")

    op.execute("DROP SEQUENCE IF EXISTS order_code_seq;")
