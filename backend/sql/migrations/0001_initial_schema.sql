-- ============================================================================
-- Migration 0001: Initial Multi-Tenant Schema + pgvector Cosine + RLS
-- Target: Supabase PostgreSQL 15+ with pgvector & Supavisor pooling
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SEQUENCE IF NOT EXISTS order_code_seq START WITH 1001 INCREMENT BY 1;

CREATE TABLE IF NOT EXISTS inventory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant' CHECK (tenant_id <> ''),
    sku VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(128) NOT NULL DEFAULT 'Hardware',
    price_vnd BIGINT NOT NULL CHECK (price_vnd >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_inventory_tenant_sku UNIQUE (tenant_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_inventory_tenant_sku ON inventory (tenant_id, sku);
CREATE INDEX IF NOT EXISTS idx_inventory_embedding_hnsw
    ON inventory USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant' CHECK (tenant_id <> ''),
    meta_psid VARCHAR(128) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    phone VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_customers_tenant_meta_psid UNIQUE (tenant_id, meta_psid)
);

CREATE INDEX IF NOT EXISTS idx_customers_tenant_psid ON customers (tenant_id, meta_psid);

CREATE TABLE IF NOT EXISTS employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant' CHECK (tenant_id <> ''),
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(64) NOT NULL DEFAULT 'fulfillment_specialist',
    telegram_user_id VARCHAR(64) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_employees_tenant_active ON employees (tenant_id, is_active);

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant' CHECK (tenant_id <> ''),
    order_code VARCHAR(64) NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    assigned_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_PAYMENT'
        CHECK (status IN ('PENDING_PAYMENT', 'PAID', 'FULFILLING', 'COMPLETED', 'CANCELLED')),
    total_amount_vnd BIGINT NOT NULL CHECK (total_amount_vnd >= 0),
    sepay_qr_url TEXT,
    sepay_transaction_id VARCHAR(128),
    items_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_orders_tenant_order_code UNIQUE (tenant_id, order_code)
);

CREATE INDEX IF NOT EXISTS idx_orders_tenant_status ON orders (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_order_code ON orders (order_code);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_sepay_transaction_id
    ON orders (sepay_transaction_id)
    WHERE sepay_transaction_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS sepay_webhook_logs (
    id BIGSERIAL PRIMARY KEY,
    sepay_transaction_id VARCHAR(128) NOT NULL UNIQUE,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant' CHECK (tenant_id <> ''),
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
CREATE INDEX IF NOT EXISTS idx_sepay_logs_order_code ON sepay_webhook_logs (order_code);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    email VARCHAR(255) NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(64) NOT NULL DEFAULT 'staff',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant_email ON users (tenant_id, email);

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

CREATE TABLE IF NOT EXISTS tenant_channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL CHECK (tenant_id <> ''),
    channel_type VARCHAR(32) NOT NULL
        CHECK (channel_type IN ('meta_page', 'sepay_account')),
    external_id VARCHAR(128) NOT NULL,
    sub_account VARCHAR(64) NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_channels_lookup UNIQUE (channel_type, external_id, sub_account)
);

CREATE INDEX IF NOT EXISTS idx_tenant_channels_tenant ON tenant_channels (tenant_id, channel_type);

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

DROP FUNCTION IF EXISTS match_inventory_embeddings(vector, varchar, integer);
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

ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_inventory ON inventory;
CREATE POLICY tenant_isolation_inventory ON inventory
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_customers ON customers;
CREATE POLICY tenant_isolation_customers ON customers
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE employees ENABLE ROW LEVEL SECURITY;
ALTER TABLE employees FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_employees ON employees;
CREATE POLICY tenant_isolation_employees ON employees
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_orders ON orders;
CREATE POLICY tenant_isolation_orders ON orders
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE sepay_webhook_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sepay_webhook_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_sepay_webhook_logs ON sepay_webhook_logs;
CREATE POLICY tenant_isolation_sepay_webhook_logs ON sepay_webhook_logs
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

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

-- ============================================================================
-- Non-Owner Application Role Guidance:
-- Create a dedicated NOBYPASSRLS role (`app_runtime`) for the backend service
-- so PostgreSQL enforces FORCE ROW LEVEL SECURITY on every tenant query:
--   CREATE ROLE app_runtime NOINHERIT LOGIN PASSWORD '<strong-secret>'
--       NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
--   GRANT USAGE ON SCHEMA public TO app_runtime;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON
--       inventory, customers, employees, orders, sepay_webhook_logs,
--       users, api_tokens, webhook_dedup_events, outbox_jobs TO app_runtime;
--   GRANT USAGE, SELECT ON SEQUENCE
--       order_code_seq, sepay_webhook_logs_id_seq, webhook_dedup_events_id_seq
--       TO app_runtime;
--   REVOKE ALL ON tenant_channels FROM PUBLIC;
--   GRANT EXECUTE ON FUNCTION
--       match_inventory_embeddings(vector, varchar, integer),
--       resolve_tenant_by_channel(text, text, text) TO app_runtime;
-- ============================================================================
