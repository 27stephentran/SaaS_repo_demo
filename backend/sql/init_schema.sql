-- ============================================================================
-- Supabase PostgreSQL + pgvector Schema Migration
-- Omnichannel AI Sales & ERP B2B SaaS MVP
-- ============================================================================

-- 1. Enable required extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Sequence for deterministic order codes (DH1001, DH1002, ...)
CREATE SEQUENCE IF NOT EXISTS order_code_seq START WITH 1001 INCREMENT BY 1;

-- 3. Companies Table (Multi-Company Isolation & Bank/SePay Metadata)
CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    name VARCHAR(255) NOT NULL,
    code VARCHAR(32) NOT NULL,
    bank_code VARCHAR(32) NOT NULL,
    bank_name VARCHAR(128) NOT NULL DEFAULT '',
    bank_account_number VARCHAR(64) NOT NULL,
    bank_account_name VARCHAR(255) NOT NULL,
    sepay_sub_account VARCHAR(64) NOT NULL DEFAULT '',
    sepay_memo_prefix VARCHAR(32) NOT NULL DEFAULT '',
    sepay_bank_account_id VARCHAR(128),
    sepay_bankhub_company_xid VARCHAR(128),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_companies_tenant_code UNIQUE (tenant_id, code)
);

CREATE INDEX IF NOT EXISTS idx_companies_tenant_active ON companies (tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_companies_tenant_code ON companies (tenant_id, code);

-- 4. Inventory Table (with 768-dimensional embedding vector)
CREATE TABLE IF NOT EXISTS inventory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    sku VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(128) NOT NULL DEFAULT 'Hardware',
    price_vnd BIGINT NOT NULL CHECK (price_vnd >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    embedding vector(768),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_inventory_tenant_sku UNIQUE (tenant_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_inventory_tenant_sku ON inventory (tenant_id, sku);
CREATE INDEX IF NOT EXISTS idx_inventory_tenant_active ON inventory (tenant_id, is_active, deactivated_at);
CREATE INDEX IF NOT EXISTS idx_inventory_tenant_company_active ON inventory (tenant_id, company_id, is_active);
CREATE INDEX IF NOT EXISTS idx_inventory_embedding_hnsw
    ON inventory USING hnsw (embedding vector_cosine_ops);

-- 5. Customers Table (Meta Graph PSID & omnichannel profile)
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    meta_psid VARCHAR(128) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    phone VARCHAR(32),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_customers_tenant_meta_psid UNIQUE (tenant_id, meta_psid)
);

CREATE INDEX IF NOT EXISTS idx_customers_tenant_psid ON customers (tenant_id, meta_psid);
CREATE INDEX IF NOT EXISTS idx_customers_tenant_active ON customers (tenant_id, is_active, deactivated_at);
CREATE INDEX IF NOT EXISTS idx_customers_tenant_company_active ON customers (tenant_id, company_id, is_active);

-- 6. Employees Table (with Telegram User ID for direct Bot API dispatch)
CREATE TABLE IF NOT EXISTS employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(64) NOT NULL DEFAULT 'fulfillment_specialist',
    telegram_user_id VARCHAR(64) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_employees_tenant_active ON employees (tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_employees_tenant_company_active ON employees (tenant_id, company_id, is_active);

-- 7. Orders Table (linking Customer, Assigned Employee, SePay VietQR & Status)
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    order_code VARCHAR(64) NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    assigned_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_PAYMENT'
        CHECK (status IN ('PENDING_PAYMENT', 'PAID', 'FULFILLING', 'COMPLETED', 'CANCELLED')),
    total_amount_vnd BIGINT NOT NULL CHECK (total_amount_vnd >= 0),
    sepay_qr_url TEXT,
    sepay_transaction_id VARCHAR(128),
    items_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_orders_tenant_order_code UNIQUE (tenant_id, order_code)
);

CREATE INDEX IF NOT EXISTS idx_orders_tenant_status ON orders (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_tenant_active ON orders (tenant_id, is_active, deactivated_at);
CREATE INDEX IF NOT EXISTS idx_orders_tenant_company_status ON orders (tenant_id, company_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_order_code ON orders (order_code);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_sepay_transaction_id
    ON orders (sepay_transaction_id)
    WHERE sepay_transaction_id IS NOT NULL;

-- 8. SePay Webhook Deduplication & Audit Log Table
CREATE TABLE IF NOT EXISTS sepay_webhook_logs (
    id BIGSERIAL PRIMARY KEY,
    sepay_transaction_id VARCHAR(128) NOT NULL UNIQUE,
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    order_code VARCHAR(64),
    transfer_type VARCHAR(16) NOT NULL,
    transfer_amount BIGINT NOT NULL CHECK (transfer_amount >= 0),
    account_number VARCHAR(64),
    sub_account VARCHAR(64),
    reference_code VARCHAR(128),
    processing_status VARCHAR(32) NOT NULL DEFAULT 'PROCESSED',
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sepay_logs_tenant_created ON sepay_webhook_logs (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sepay_logs_tenant_company_created ON sepay_webhook_logs (tenant_id, company_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sepay_logs_order_code ON sepay_webhook_logs (order_code);

-- 9. Users Table (Tenant-scoped authentication accounts with RBAC & company binding)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    email VARCHAR(255) NOT NULL,
    username VARCHAR(64),
    full_name VARCHAR(255) NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role VARCHAR(64) NOT NULL DEFAULT 'staff',
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    password_changed_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email),
    CONSTRAINT ck_users_role_valid CHECK (role IN ('admin', 'manager', 'staff', 'demo')),
    CONSTRAINT ck_users_company_required_for_non_admin CHECK (role IN ('admin', 'demo') OR company_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant_email ON users (tenant_id, email);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_tenant_username
    ON users (tenant_id, LOWER(username))
    WHERE username IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_tenant_company ON users (tenant_id, company_id);

-- 10. API Tokens Table (Access/Refresh JTI revocation & token hash tracking)
CREATE TABLE IF NOT EXISTS api_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    jti VARCHAR(128) NOT NULL UNIQUE,
    token_hash TEXT NOT NULL,
    token_type VARCHAR(32) NOT NULL DEFAULT 'refresh'
        CHECK (token_type IN ('access', 'refresh')),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_tenant_user ON api_tokens (tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_token_hash ON api_tokens (token_hash);

-- 11. Tenant Channels Table (Meta Page ID / SePay Account -> Tenant & Company routing)
CREATE TABLE IF NOT EXISTS tenant_channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    channel_type VARCHAR(32) NOT NULL
        CHECK (channel_type IN ('meta_page', 'sepay_account')),
    external_id VARCHAR(128) NOT NULL,
    sub_account VARCHAR(64) NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_channels_lookup UNIQUE (channel_type, external_id, sub_account)
);

CREATE INDEX IF NOT EXISTS idx_tenant_channels_tenant ON tenant_channels (tenant_id, channel_type);
CREATE INDEX IF NOT EXISTS idx_tenant_channels_tenant_company ON tenant_channels (tenant_id, company_id, channel_type);

-- 12. Webhook Deduplication Events Table (Multi-provider idempotency)
CREATE TABLE IF NOT EXISTS webhook_dedup_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    provider VARCHAR(32) NOT NULL,
    external_event_id VARCHAR(128) NOT NULL,
    payload_hash VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_webhook_dedup_tenant_provider_event UNIQUE (tenant_id, provider, external_event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_dedup_tenant_created ON webhook_dedup_events (tenant_id, created_at DESC);

-- 13. Outbox Jobs Table (Transactional outbox for async Telegram/Meta/AI retries)
CREATE TABLE IF NOT EXISTS outbox_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    job_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'DEAD_LETTER')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbox_jobs_status_retry ON outbox_jobs (status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_outbox_jobs_tenant_status ON outbox_jobs (tenant_id, status);

-- 14. Omnichannel Conversations & Messages Tables
CREATE TABLE IF NOT EXISTS channel_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    channel VARCHAR(32) NOT NULL
        CHECK (channel IN ('messenger', 'instagram', 'telegram', 'web', 'other')),
    external_thread_id VARCHAR(128) NOT NULL,
    customer_name VARCHAR(255) NOT NULL DEFAULT 'Customer',
    last_message_preview TEXT NOT NULL DEFAULT '',
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unread_count INTEGER NOT NULL DEFAULT 0 CHECK (unread_count >= 0),
    status VARCHAR(32) NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_conversations_thread
    ON channel_conversations (
        tenant_id,
        COALESCE(company_id, '00000000-0000-0000-0000-000000000000'::uuid),
        channel,
        external_thread_id
    );
CREATE INDEX IF NOT EXISTS idx_channel_conversations_lookup
    ON channel_conversations (tenant_id, company_id, channel, last_message_at DESC);

CREATE TABLE IF NOT EXISTS channel_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    company_id UUID REFERENCES companies(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES channel_conversations(id) ON DELETE CASCADE,
    channel VARCHAR(32) NOT NULL
        CHECK (channel IN ('messenger', 'instagram', 'telegram', 'web', 'other')),
    external_message_id VARCHAR(128),
    direction VARCHAR(16) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    sender_type VARCHAR(16) NOT NULL
        CHECK (sender_type IN ('customer', 'ai_bot', 'manager', 'admin')),
    sender_name VARCHAR(255) NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    order_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_channel_messages_conv_created
    ON channel_messages (conversation_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_channel_messages_tenant_company_created
    ON channel_messages (tenant_id, company_id, created_at DESC);

-- 15. Semantic Inventory Search Helper using `<=>` Cosine Distance Operator
DROP FUNCTION IF EXISTS match_inventory_embeddings(vector, varchar, integer);
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

-- 16. SECURITY DEFINER Channel-to-Tenant & Company Resolvers (run prior to set_config)
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

-- 17. Row Level Security (Fail-Closed Tenant & Optional Company Isolation)
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_companies ON companies;
CREATE POLICY tenant_isolation_companies ON companies
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_inventory ON inventory;
CREATE POLICY tenant_isolation_inventory ON inventory
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

ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_customers ON customers;
CREATE POLICY tenant_isolation_customers ON customers
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

ALTER TABLE employees ENABLE ROW LEVEL SECURITY;
ALTER TABLE employees FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_employees ON employees;
CREATE POLICY tenant_isolation_employees ON employees
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

ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_orders ON orders;
CREATE POLICY tenant_isolation_orders ON orders
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

ALTER TABLE sepay_webhook_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sepay_webhook_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_sepay_webhook_logs ON sepay_webhook_logs;
CREATE POLICY tenant_isolation_sepay_webhook_logs ON sepay_webhook_logs
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

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_users ON users;
CREATE POLICY tenant_isolation_users ON users
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_tokens FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_api_tokens ON api_tokens;
CREATE POLICY tenant_isolation_api_tokens ON api_tokens
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE webhook_dedup_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_dedup_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_webhook_dedup_events ON webhook_dedup_events;
CREATE POLICY tenant_isolation_webhook_dedup_events ON webhook_dedup_events
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE outbox_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox_jobs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_outbox_jobs ON outbox_jobs;
CREATE POLICY tenant_isolation_outbox_jobs ON outbox_jobs
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE channel_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_conversations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_channel_conversations ON channel_conversations;
CREATE POLICY tenant_isolation_channel_conversations ON channel_conversations
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

ALTER TABLE channel_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_channel_messages ON channel_messages;
CREATE POLICY tenant_isolation_channel_messages ON channel_messages
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

-- ============================================================================
-- 18. Non-Owner Application Role Setup (Production RLS Enforcement)
-- ============================================================================
-- IMPORTANT: PostgreSQL superusers and roles with BYPASSRLS bypass RLS even
-- when FORCE ROW LEVEL SECURITY is enabled. In production (Supabase / RDS),
-- connect the FastAPI backend using a dedicated non-owner, NOBYPASSRLS role:
--
--   CREATE ROLE app_runtime NOINHERIT LOGIN PASSWORD '<strong-secret>'
--       NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
--   GRANT USAGE ON SCHEMA public TO app_runtime;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON
--       companies, inventory, customers, employees, orders, sepay_webhook_logs,
--       users, api_tokens, webhook_dedup_events, outbox_jobs,
--       channel_conversations, channel_messages
--       TO app_runtime;
--   GRANT USAGE, SELECT ON SEQUENCE
--       order_code_seq, sepay_webhook_logs_id_seq, webhook_dedup_events_id_seq
--       TO app_runtime;
--   REVOKE ALL ON tenant_channels FROM PUBLIC;
--   GRANT EXECUTE ON FUNCTION
--       match_inventory_embeddings(vector, varchar, integer),
--       resolve_tenant_by_channel(text, text, text),
--       resolve_channel_context(text, text, text)
--       TO app_runtime;
-- ============================================================================
