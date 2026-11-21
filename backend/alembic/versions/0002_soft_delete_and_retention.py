"""0002 soft-delete inactivation (`is_active`, `deactivated_at`) and retention indexes.

Revision ID: 0002_soft_delete_and_retention
Revises: 0001_initial_schema
Create Date: 2026-11-20 01:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_soft_delete_and_retention"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add soft-delete columns/indexes and update pgvector search to exclude inactive rows."""
    op.add_column(
        "inventory",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
    )
    op.add_column(
        "inventory",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_inventory_tenant_active",
        "inventory",
        ["tenant_id", "is_active", "deactivated_at"],
    )

    op.add_column(
        "customers",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
    )
    op.add_column(
        "customers",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_customers_tenant_active",
        "customers",
        ["tenant_id", "is_active", "deactivated_at"],
    )

    op.add_column(
        "employees",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "orders",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
    )
    op.add_column(
        "orders",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_orders_tenant_active",
        "orders",
        ["tenant_id", "is_active", "deactivated_at"],
    )

    op.execute("DROP FUNCTION IF EXISTS match_inventory_embeddings(vector, varchar, integer);")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION match_inventory_embeddings(
            query_embedding vector(768),
            match_tenant_id VARCHAR(64),
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
              AND i.is_active = TRUE
              AND i.embedding IS NOT NULL
            ORDER BY i.embedding <=> query_embedding ASC
            LIMIT match_limit;
        $$;
        """
    )


def downgrade() -> None:
    """Revert match_inventory_embeddings function, soft-delete columns, and indexes."""
    op.execute("DROP FUNCTION IF EXISTS match_inventory_embeddings(vector, varchar, integer);")
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

    op.drop_index("idx_orders_tenant_active", table_name="orders")
    op.drop_column("orders", "deactivated_at")
    op.drop_column("orders", "is_active")

    op.drop_column("employees", "deactivated_at")

    op.drop_index("idx_customers_tenant_active", table_name="customers")
    op.drop_column("customers", "deactivated_at")
    op.drop_column("customers", "is_active")

    op.drop_index("idx_inventory_tenant_active", table_name="inventory")
    op.drop_column("inventory", "deactivated_at")
    op.drop_column("inventory", "is_active")
