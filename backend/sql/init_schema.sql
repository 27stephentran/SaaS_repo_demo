-- ============================================================================
-- Supabase PostgreSQL + pgvector Schema Migration
-- Omnichannel AI Sales & ERP B2B SaaS MVP
-- ============================================================================

-- 1. Enable pgvector extension for 768-dim Google Gemini `text-embedding-004`
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Inventory Table (with 768-dimensional embedding vector)
CREATE TABLE IF NOT EXISTS inventory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant',
    sku VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    category VARCHAR(128) NOT NULL DEFAULT 'Hardware',
    price_vnd BIGINT NOT NULL CHECK (price_vnd >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inventory_tenant_sku ON inventory (tenant_id, sku);

-- 3. Customers Table (Meta Graph PSID & omnichannel profile)
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant',
    meta_psid VARCHAR(128) NOT NULL UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    phone VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_customers_tenant_psid ON customers (tenant_id, meta_psid);

-- 4. Employees Table (with Telegram User ID for direct Bot API dispatch)
CREATE TABLE IF NOT EXISTS employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant',
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(64) NOT NULL DEFAULT 'fulfillment_specialist',
    telegram_user_id VARCHAR(64) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_employees_tenant_active ON employees (tenant_id, is_active);

-- 5. Orders Table (linking Customer, Assigned Employee, SePay VietQR & Status)
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default-tenant',
    order_code VARCHAR(64) NOT NULL UNIQUE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    assigned_employee_id UUID REFERENCES employees(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_PAYMENT'
        CHECK (status IN ('PENDING_PAYMENT', 'PAID', 'FULFILLING', 'COMPLETED', 'CANCELLED')),
    total_amount_vnd BIGINT NOT NULL CHECK (total_amount_vnd >= 0),
    sepay_qr_url TEXT,
    sepay_transaction_id VARCHAR(128),
    items_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_tenant_status ON orders (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_order_code ON orders (order_code);

-- 6. Semantic Inventory Search Helper using `<->` Cosine Distance Operator
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
    distance DOUBLE PRECISION
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
        (i.embedding <-> query_embedding) AS distance
    FROM inventory i
    WHERE i.tenant_id = match_tenant_id
      AND i.embedding IS NOT NULL
    ORDER BY i.embedding <-> query_embedding ASC
    LIMIT match_limit;
$$;
