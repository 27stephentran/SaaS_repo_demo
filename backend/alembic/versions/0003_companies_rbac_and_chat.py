"""0003 companies, RBAC hierarchy, password rotation, and omnichannel chat persistence.

Revision ID: 0003_companies_rbac_and_chat
Revises: 0002_soft_delete_and_retention
Create Date: 2026-11-21 05:15:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_companies_rbac_and_chat"
down_revision: str | None = "0002_soft_delete_and_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMPANY_RLS_TABLES: tuple[str, ...] = (
    "inventory",
    "customers",
    "employees",
    "orders",
    "sepay_webhook_logs",
    "channel_conversations",
    "channel_messages",
)

EXISTING_COMPANY_SCOPED_TABLES: tuple[str, ...] = (
    "inventory",
    "customers",
    "employees",
    "orders",
    "sepay_webhook_logs",
)

TABLES_WITH_LEGACY_TENANT_DEFAULT: tuple[str, ...] = (
    "inventory",
    "customers",
    "employees",
    "orders",
    "sepay_webhook_logs",
)


def upgrade() -> None:
    """Add companies, RBAC user columns, company_id scoping, chat tables, and RLS."""
    op.create_table(
        "companies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("bank_code", sa.String(length=32), nullable=False),
        sa.Column("bank_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("bank_account_number", sa.String(length=64), nullable=False),
        sa.Column("bank_account_name", sa.String(length=255), nullable=False),
        sa.Column("sepay_sub_account", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("sepay_memo_prefix", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("sepay_bank_account_id", sa.String(length=128), nullable=True),
        sa.Column("sepay_bankhub_company_xid", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("tenant_id <> ''", name="ck_companies_tenant_non_empty"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_companies_tenant_code"),
    )
    op.create_index("idx_companies_tenant_active", "companies", ["tenant_id", "is_active"])
    op.create_index("idx_companies_tenant_code", "companies", ["tenant_id", "code"])

    op.create_table(
        "channel_conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="RESTRICT", name="fk_channel_conv_company_id"),
            nullable=True,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL", name="fk_channel_conv_customer_id"),
            nullable=True,
        ),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_thread_id", sa.String(length=128), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=False, server_default="Customer"),
        sa.Column("last_message_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("tenant_id <> ''", name="ck_channel_conversations_tenant_non_empty"),
        sa.CheckConstraint(
            "channel IN ('messenger', 'instagram', 'telegram', 'web', 'other')",
            name="ck_channel_conversations_channel",
        ),
        sa.CheckConstraint(
            "unread_count >= 0",
            name="ck_channel_conversations_unread_non_negative",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_conversations_thread "
        "ON channel_conversations ("
        "tenant_id, "
        "COALESCE(company_id, '00000000-0000-0000-0000-000000000000'::uuid), "
        "channel, "
        "external_thread_id"
        ");"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_conversations_lookup "
        "ON channel_conversations (tenant_id, company_id, channel, last_message_at DESC);"
    )

    op.create_table(
        "channel_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="RESTRICT", name="fk_channel_msg_company_id"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "channel_conversations.id",
                ondelete="CASCADE",
                name="fk_channel_msg_conversation_id",
            ),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_message_id", sa.String(length=128), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("sender_type", sa.String(length=16), nullable=False),
        sa.Column("sender_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("order_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("tenant_id <> ''", name="ck_channel_messages_tenant_non_empty"),
        sa.CheckConstraint(
            "channel IN ('messenger', 'instagram', 'telegram', 'web', 'other')",
            name="ck_channel_messages_channel",
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_channel_messages_direction",
        ),
        sa.CheckConstraint(
            "sender_type IN ('customer', 'ai_bot', 'manager', 'admin')",
            name="ck_channel_messages_sender_type",
        ),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_messages_conv_created "
        "ON channel_messages (conversation_id, created_at ASC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_channel_messages_tenant_company_created "
        "ON channel_messages (tenant_id, company_id, created_at DESC);"
    )

    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    op.add_column(
        "users",
        sa.Column("full_name", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "users",
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="RESTRICT", name="fk_users_company_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("employees.id", ondelete="SET NULL", name="fk_users_employee_id"),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_tenant_username "
        "ON users (tenant_id, LOWER(username)) WHERE username IS NOT NULL;"
    )
    op.create_index("idx_users_tenant_company", "users", ["tenant_id", "company_id"])

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM users
                WHERE role NOT IN ('admin', 'manager', 'staff', 'demo')
            ) THEN
                ALTER TABLE users
                    ADD CONSTRAINT ck_users_role_valid
                    CHECK (role IN ('admin', 'manager', 'staff', 'demo'));
            ELSE
                ALTER TABLE users
                    ADD CONSTRAINT ck_users_role_valid
                    CHECK (role IN ('admin', 'manager', 'staff', 'demo')) NOT VALID;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM users
                WHERE role NOT IN ('admin', 'demo') AND company_id IS NULL
            ) THEN
                ALTER TABLE users
                    ADD CONSTRAINT ck_users_company_required_for_non_admin
                    CHECK (role IN ('admin', 'demo') OR company_id IS NOT NULL);
            ELSE
                ALTER TABLE users
                    ADD CONSTRAINT ck_users_company_required_for_non_admin
                    CHECK (role IN ('admin', 'demo') OR company_id IS NOT NULL) NOT VALID;
            END IF;
        END $$;
        """
    )

    for table_name in (
        "inventory",
        "customers",
        "employees",
        "orders",
        "sepay_webhook_logs",
        "tenant_channels",
    ):
        op.add_column(
            table_name,
            sa.Column(
                "company_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "companies.id",
                    ondelete="RESTRICT",
                    name=f"fk_{table_name}_company_id",
                ),
                nullable=True,
            ),
        )

    op.create_index(
        "idx_inventory_tenant_company_active",
        "inventory",
        ["tenant_id", "company_id", "is_active"],
    )
    op.create_index(
        "idx_customers_tenant_company_active",
        "customers",
        ["tenant_id", "company_id", "is_active"],
    )
    op.create_index(
        "idx_employees_tenant_company_active",
        "employees",
        ["tenant_id", "company_id", "is_active"],
    )
    op.create_index(
        "idx_orders_tenant_company_status",
        "orders",
        ["tenant_id", "company_id", "status"],
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sepay_logs_tenant_company_created "
        "ON sepay_webhook_logs (tenant_id, company_id, created_at DESC);"
    )
    op.create_index(
        "idx_tenant_channels_tenant_company",
        "tenant_channels",
        ["tenant_id", "company_id", "channel_type"],
    )

    for table_name in TABLES_WITH_LEGACY_TENANT_DEFAULT:
        op.alter_column(table_name, "tenant_id", server_default=None)

    op.execute("ALTER TABLE companies ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE companies FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_companies ON companies
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));
        """
    )

    for new_chat_table in ("channel_conversations", "channel_messages"):
        op.execute(f"ALTER TABLE {new_chat_table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {new_chat_table} FORCE ROW LEVEL SECURITY;")

    for table_name in COMPANY_RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name};")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table_name} ON {table_name}
                USING (
                    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
                    AND (
                        NULLIF(current_setting('app.company_id', true), '') IS NULL
                        OR company_id = NULLIF(current_setting('app.company_id', true), '')::uuid
                    )
                )
                WITH CHECK (
                    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')
                    AND (
                        NULLIF(current_setting('app.company_id', true), '') IS NULL
                        OR company_id = NULLIF(current_setting('app.company_id', true), '')::uuid
                    )
                );
            """
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION resolve_channel_context(
            p_channel_type TEXT,
            p_external_id TEXT,
            p_sub_account TEXT DEFAULT ''
        )
        RETURNS TABLE (
            tenant_id VARCHAR(64),
            company_id UUID
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT tc.tenant_id, tc.company_id
            FROM tenant_channels tc
            WHERE tc.channel_type = p_channel_type
              AND tc.external_id = p_external_id
              AND tc.sub_account = COALESCE(p_sub_account, '')
              AND tc.is_active = TRUE
            LIMIT 1;
        $$;
        """
    )


def downgrade() -> None:
    """Revert companies, RBAC columns, chat tables, and company-aware RLS policies."""
    op.execute("DROP FUNCTION IF EXISTS resolve_channel_context(TEXT, TEXT, TEXT);")

    for table_name in EXISTING_COMPANY_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name};")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table_name} ON {table_name}
                USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));
            """
        )

    for table_name in ("channel_messages", "channel_conversations", "companies"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name};")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;")

    for table_name in TABLES_WITH_LEGACY_TENANT_DEFAULT:
        op.alter_column(table_name, "tenant_id", server_default="default-tenant")

    op.drop_index("idx_tenant_channels_tenant_company", table_name="tenant_channels")
    op.execute("DROP INDEX IF EXISTS idx_sepay_logs_tenant_company_created;")
    op.drop_index("idx_orders_tenant_company_status", table_name="orders")
    op.drop_index("idx_employees_tenant_company_active", table_name="employees")
    op.drop_index("idx_customers_tenant_company_active", table_name="customers")
    op.drop_index("idx_inventory_tenant_company_active", table_name="inventory")

    for table_name in (
        "tenant_channels",
        "sepay_webhook_logs",
        "orders",
        "employees",
        "customers",
        "inventory",
    ):
        op.drop_constraint(f"fk_{table_name}_company_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "company_id")

    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_company_required_for_non_admin;")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role_valid;")
    op.drop_index("idx_users_tenant_company", table_name="users")
    op.execute("DROP INDEX IF EXISTS uq_users_tenant_username;")
    op.drop_constraint("fk_users_employee_id", "users", type_="foreignkey")
    op.drop_constraint("fk_users_company_id", "users", type_="foreignkey")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "employee_id")
    op.drop_column("users", "company_id")
    op.drop_column("users", "full_name")
    op.drop_column("users", "username")

    op.execute("DROP INDEX IF EXISTS idx_channel_messages_tenant_company_created;")
    op.execute("DROP INDEX IF EXISTS idx_channel_messages_conv_created;")
    op.drop_table("channel_messages")

    op.execute("DROP INDEX IF EXISTS idx_channel_conversations_lookup;")
    op.execute("DROP INDEX IF EXISTS uq_channel_conversations_thread;")
    op.drop_table("channel_conversations")

    op.drop_index("idx_companies_tenant_code", table_name="companies")
    op.drop_index("idx_companies_tenant_active", table_name="companies")
    op.drop_table("companies")
