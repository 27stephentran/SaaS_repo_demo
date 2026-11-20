# SaaS_repo_demo — Omnichannel AI Sales & ERP B2B SaaS MVP

> **Status:** MVP In Development  
> **Category:** Commercial B2B SaaS (Omnichannel AI Sales, RAG Inventory & VietQR Order Fulfillment ERP)  
> **License:** Proprietary — All Rights Reserved ([`LICENSE.md`](./LICENSE.md))

---

## 1. Executive Summary & Product Vision

- **Product Name:** `SaaS_repo_demo` (Omnichannel AI Sales & ERP B2B SaaS)
- **Target Customer (ICP):** High-velocity social commerce merchants, D2C brands, and B2B omnichannel retail operators in Southeast Asia.
- **Core Value Proposition:** Unifies Meta social commerce conversations (Messenger/Instagram), semantic product discovery via Google Gemini RAG + `pgvector`, instant dynamic VietQR payment generation/verification via SePay IPN, and real-time staff fulfillment dispatch on Telegram—backed by a live Nuxt 3 ERP Admin Panel.
- **Business & Monetization Model:** Commercial B2B subscription SaaS (tiered recurring revenue based on monthly order volume & connected channels).

---

## 2. Definitive Technical Architecture & Stack

| Layer | Technology | Implementation Details |
| :--- | :--- | :--- |
| **Backend & Core Orchestrator** | **Python 3.11+ & FastAPI + Uvicorn** | Non-blocking `async/await` webhook ingestion, `asyncpg` + SQLAlchemy 2.0 async ORM, Alembic migrations, HS256 JWT auth with JTI revocation, and `httpx` + `tenacity` clients. Containerized via non-root `backend/Dockerfile` for **Render** and `docker-compose.yml`. |
| **Database & Vector Engine** | **Supabase (PostgreSQL + `pgvector`)** | Single source of truth for multi-tenant RLS-enforced ERP/auth tables (`inventory`, `customers`, `employees`, `orders`, `users`, `api_tokens`, `tenant_channels`, `sepay_webhook_logs`, `webhook_dedup_events`, `outbox_jobs`) and 768-dimensional embeddings using `<=>` cosine distance (`HNSW`). |
| **Frontend (ERP Admin Panel)** | **Vue 3 & Nuxt 3 + TypeScript + Nuxt UI + Pinia** | Deployed on **Vercel** or `frontend/Dockerfile`. Uses a **Nuxt Nitro BFF proxy** (`server/api/auth/*`, `server/api/erp/[...path].ts`) with HttpOnly session cookies, same-origin CSRF checks, `/login`, Inventory Datatable, Kanban board, and webhook telemetry. |
| **AI & RAG Engine** | **Google Gemini API (`google-genai` SDK)** | `gemini-1.5-flash` / `gemini-1.5-pro` for natural Vietnamese/English sales consultations and intent extraction; `text-embedding-004` (768 dims) for product semantic indexing. |
| **Customer Chat Integration** | **Meta Graph API (v18.0+)** | Webhooks (`GET`/`POST /api/v1/webhooks/meta`) with `X-Hub-Signature-256` verification, Send API, and Private Replies API for automated comment-to-DM and inbox consultations. |
| **Payment & VietQR Gateway** | **SePay Sandbox API & IPN Webhooks** | Dynamic VietQR generation embedding deterministic order codes (`DH<ORDER_ID>`) and automated bank transfer verification via `POST /api/v1/webhooks/sepay` (HMAC-SHA256 replay window / Apikey + idempotency). |
| **Staff Fulfillment Dispatch** | **Telegram Bot API (`sendMessage`)** | Lightweight HTTP POST dispatcher pushing instant packing/shipping alerts directly to assigned staff `telegram_user_id` upon payment confirmation. |

---

## 3. End-to-End Omnichannel Workflow

```text
[Customer on Meta] ──(1. Comment/DM)──► [FastAPI /api/v1/webhooks/meta]
                                                │
       ┌────────────────────────────────────────┴────────────────────────────────────────┐
       ▼                                                                                 ▼
[Supabase pgvector (`<=>`)]                                              [Google Gemini 1.5 Flash/Pro]
Semantic Inventory Retrieval (`text-embedding-004`)                      Grounded Sales Consultation & Order Intent
       │                                                                                 │
       └────────────────────────────────────────┬────────────────────────────────────────┘
                                                ▼
                              [Order Created + SePay Dynamic VietQR]
                                                │
                                     (2. Customer Scans QR)
                                                │
                                                ▼
                              [SePay IPN -> /api/v1/webhooks/sepay]
                                                │
                       ┌────────────────────────┴────────────────────────┐
                       ▼                                                 ▼
        [Nuxt 3 ERP Kanban Updated -> PAID]             [Telegram Bot API `sendMessage`]
        Real-time Pinia Store Sync                      Instant Alert to Staff `telegram_user_id`
```

---

## 4. Database Schema & Migrations (`backend/alembic/` & `backend/sql/init_schema.sql`)

Managed via **Alembic** (`backend/alembic/versions/0001_initial_schema.py`) with bootstrap parity in `backend/sql/init_schema.sql` (including PostgreSQL Row-Level Security `FORCE ROW LEVEL SECURITY` scoped to `app.tenant_id` for non-owner `app_runtime` `NOBYPASSRLS` roles):

- **`inventory`**: `id`, `tenant_id`, `sku`, `name`, `description`, `category`, `price_vnd`, `stock_quantity`, `embedding vector(768)` (HNSW `vector_cosine_ops`), `created_at`, `updated_at` + `match_inventory_embeddings()` function using `<=>` cosine distance operator.
- **`customers` / `employees` / `orders`**: Multi-tenant CRM, Telegram staff dispatch targets, and VietQR order lifecycle (`PENDING_PAYMENT` -> `PAID` -> `FULFILLING` -> `COMPLETED` / `CANCELLED`).
- **`users` / `api_tokens`**: Per-tenant scrypt accounts and JWT JTI revocation/refresh tracking.
- **`tenant_channels` / `sepay_webhook_logs` / `webhook_dedup_events` / `outbox_jobs`**: Channel-to-tenant resolution (`resolve_tenant_by_channel()`), SePay IPN audit trail, webhook idempotency, and async outbox jobs.

---

## 5. Repository Structure

```text
SaaS_repo_demo/
├── backend/
│   ├── Dockerfile                # Non-root Python 3.11 slim container w/ /health probe
│   ├── alembic.ini               # Alembic async migration configuration
│   ├── alembic/                  # Migration environment & versioned schema revisions
│   ├── pyproject.toml            # Python >=3.11 metadata & Pytest/Ruff/Pyright config
│   ├── requirements.txt          # Runtime dependencies (FastAPI, SQLAlchemy, Alembic, tenacity)
│   ├── requirements-dev.txt      # Dev/test dependencies (-r requirements.txt + Pytest/Ruff/Pyright)
│   ├── sql/
│   │   └── init_schema.sql       # Supabase PostgreSQL + pgvector + RLS bootstrap script
│   ├── app/
│   │   ├── main.py               # FastAPI entrypoint & deep `/health` DB probe
│   │   ├── api/
│   │   │   ├── auth.py           # JWT login/refresh/logout & tenant auth endpoints
│   │   │   ├── erp.py            # Authenticated inventory, vector search, orders, customers, employees
│   │   │   └── webhooks.py       # Signed Meta Graph v18.0 & SePay IPN webhook endpoints
│   │   ├── core/
│   │   │   ├── auth.py           # HS256 JWT signing/verification, scrypt hashing, JTI revocation
│   │   │   ├── config.py         # Fail-closed Pydantic Settings configuration
│   │   │   └── database.py       # Async SQLAlchemy + RLS `tenant_db_session` + pgvector `<=>` search
│   │   ├── models/
│   │   │   └── entities.py       # SQLAlchemy 2.0 ORM models & Pydantic v2 schemas
│   │   └── services/
│   │       ├── ai_rag.py         # Google Gemini (`google-genai`) RAG & embeddings engine
│   │       ├── integrations.py   # Async httpx + tenacity clients (Meta v18.0, SePay, Telegram)
│   │       └── order_service.py  # Atomic order creation, stock checks, and state transitions
│   └── tests/                    # End-to-end, security P0, Phase 2, and Phase 3 pytest suites
├── frontend/
│   ├── Dockerfile                # Multi-stage non-root Node 22 Alpine container
│   ├── package.json              # Nuxt 3 + Vue 3 + TypeScript + @nuxt/ui + Pinia + Vitest
│   ├── nuxt.config.ts            # Nuxt 3 configuration & Nitro runtimeConfig
│   ├── tsconfig.json             # Strict TypeScript configuration
│   ├── app.vue                   # Root layout wrapper
│   ├── server/
│   │   ├── api/                  # Nitro BFF proxy (`auth/*`, `erp/[...path].ts`)
│   │   └── utils/                # HttpOnly cookie session & same-origin CSRF helpers
│   ├── types/
│   │   └── erp.ts                # Strict TypeScript interfaces matching FastAPI schemas
│   ├── stores/
│   │   ├── erp.ts                # Pinia store for ERP state, Kanban & webhook sync
│   │   └── inventory.ts          # Pinia inventory store export alias
│   ├── pages/
│   │   ├── index.vue             # Nuxt UI ERP Admin Dashboard (Inventory + Order Kanban)
│   │   └── login.vue             # Tenant login view backed by Nitro BFF auth
│   └── tests/                    # Vitest store & Nitro BFF security tests
├── .github/workflows/ci.yml      # GitHub Actions CI (Alembic + pgvector, Ruff/Pyright/Pytest, Nuxt/Vitest)
├── docker-compose.yml            # Full-stack Compose (pgvector Postgres, Backend, Frontend)
├── render.yaml                   # Production Render Blueprint (non-wildcard CORS, fail-closed env)
├── .env.example                  # Documented environment variable template
├── .gitignore                    # Strict git hygiene (.harness/, .env, caches ignored)
├── LICENSE.md                    # Proprietary — All Rights Reserved
└── README.md                     # Architecture & MVP specification
```

---

## 6. Quickstart & Verification

### 6.1 Backend (FastAPI + Alembic + Pytest)
```bash
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
pytest -v
```

### 6.2 Docker Compose (Full Stack + Render Parity)
```bash
cp .env.example .env
docker compose up --build
# Optional: sync Alembic revision on an init_schema.sql-bootstrapped DB:
# docker compose exec backend alembic stamp head
```

### 6.3 Frontend (Nuxt 3 + Nitro BFF + Vitest)
```bash
cd frontend
npm ci
npm run typecheck
npm run build
npm test
npm run dev
```

---

## 7. Security, Multi-Tenancy & Compliance Invariants

1. **Tenant Isolation (`DOM-01`):** Every database table and API query is scoped to `tenant_id` and enforced via PostgreSQL `FORCE ROW LEVEL SECURITY` (`app.tenant_id`) with a non-owner `NOBYPASSRLS` role in production.
2. **Auth & Nitro BFF (`SEC-02`):** Backend HS256 JWT tokens with JTI revocation; Nuxt Nitro BFF stores auth tokens in `HttpOnly` cookies with same-origin CSRF enforcement and strict path-allowlist proxy validation.
3. **Secret & Container Hygiene (`SEC-01`):** Never commit `.env` or private keys (`*.pem`, `*.key`); containers run as non-root users (`appuser` / `node`) with pinned non-wildcard `CORS_ORIGINS`.
4. **Payment & Webhook Verification:** Verify Meta `X-Hub-Signature-256` / `hub.verify_token` and SePay HMAC-SHA256 replay-window / authorization headers before mutating order state.
