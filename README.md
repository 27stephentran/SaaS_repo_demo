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
| **Backend & Core Orchestrator** | **Python 3.11+ & FastAPI + Uvicorn** | Non-blocking `async/await` webhook ingestion, `asyncpg` + SQLAlchemy 2.0 async ORM, `httpx` async clients for external APIs. Containerized via `backend/Dockerfile` for **Render** deployment and local `docker-compose.yml` parity. |
| **Database & Vector Engine** | **Supabase (PostgreSQL + `pgvector`)** | Single source of truth for relational ERP tables (`inventory`, `customers`, `employees`, `orders`) and 768-dimensional vector embeddings using `<->` cosine distance similarity search. |
| **Frontend (ERP Admin Panel)** | **Vue 3 & Nuxt 3 + TypeScript + Nuxt UI + Pinia** | Deployed on **Vercel**. Provides reactive Inventory Datatables, Order Fulfillment Kanban boards, and live omnichannel webhook telemetry. |
| **AI & RAG Engine** | **Google Gemini API (`google-genai` SDK)** | `gemini-1.5-flash` / `gemini-1.5-pro` for natural Vietnamese/English sales consultations and intent extraction; `text-embedding-004` (768 dims) for product semantic indexing. |
| **Customer Chat Integration** | **Meta Graph API (v18.0+)** | Webhooks (`GET`/`POST /api/v1/webhooks/meta`), Send API, and Private Replies API for automated comment-to-DM and inbox consultations. |
| **Payment & VietQR Gateway** | **SePay Sandbox API & IPN Webhooks** | Dynamic VietQR generation embedding deterministic order codes (`DH<ORDER_ID>`) and automated bank transfer verification via `POST /api/v1/webhooks/sepay`. |
| **Staff Fulfillment Dispatch** | **Telegram Bot API (`sendMessage`)** | Lightweight HTTP POST dispatcher pushing instant packing/shipping alerts directly to assigned staff `telegram_user_id` upon payment confirmation. |

---

## 3. End-to-End Omnichannel Workflow

```text
[Customer on Meta] ──(1. Comment/DM)──► [FastAPI /api/v1/webhooks/meta]
                                                │
       ┌────────────────────────────────────────┴────────────────────────────────────────┐
       ▼                                                                                 ▼
[Supabase pgvector (`<->`)]                                              [Google Gemini 1.5 Flash/Pro]
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

## 4. Database Schema (`backend/sql/init_schema.sql`)

- **`inventory`**: `id`, `tenant_id`, `sku`, `name`, `description`, `price_vnd`, `stock_quantity`, `embedding vector(768)`, `created_at`, `updated_at` + `match_inventory_embeddings()` function using `<->` cosine distance operator.
- **`customers`**: `id`, `tenant_id`, `meta_psid`, `full_name`, `phone`, `created_at`.
- **`employees`**: `id`, `tenant_id`, `full_name`, `role`, `telegram_user_id`, `is_active`, `created_at`.
- **`orders`**: `id`, `tenant_id`, `order_code`, `customer_id`, `assigned_employee_id`, `status` (`PENDING_PAYMENT`, `PAID`, `FULFILLING`, `COMPLETED`, `CANCELLED`), `total_amount_vnd`, `sepay_qr_url`, `sepay_transaction_id`, `items_json`, `created_at`, `updated_at`.

---

## 5. Repository Structure

```text
SaaS_repo_demo/
├── backend/
│   ├── Dockerfile                # Render-ready Python 3.11 slim container
│   ├── pyproject.toml            # Python project metadata & Pytest/Ruff/Pyright config
│   ├── requirements.txt          # Pinned runtime & test dependencies
│   ├── sql/
│   │   └── init_schema.sql       # Supabase PostgreSQL + pgvector migration
│   ├── app/
│   │   ├── main.py               # FastAPI application entrypoint
│   │   ├── api/
│   │   │   ├── erp.py            # Inventory, vector search, orders, customers, employees API
│   │   │   └── webhooks.py       # Meta Graph v18.0 & SePay IPN webhook endpoints
│   │   ├── core/
│   │   │   ├── config.py         # Pydantic Settings configuration
│   │   │   └── database.py       # Async SQLAlchemy + asyncpg + pgvector `<->` search
│   │   ├── models/
│   │   │   └── entities.py       # SQLAlchemy 2.0 ORM models & Pydantic v2 schemas
│   │   └── services/
│   │       ├── ai_rag.py         # Google Gemini (`google-genai`) RAG & embeddings engine
│   │       └── integrations.py   # Async httpx clients (Meta v18.0, SePay, Telegram)
│   └── tests/
│       └── test_api.py           # End-to-end async pytest suite
├── frontend/
│   ├── package.json              # Nuxt 3 + Vue 3 + TypeScript + @nuxt/ui + Pinia
│   ├── nuxt.config.ts            # Nuxt 3 configuration
│   ├── tsconfig.json             # Strict TypeScript configuration
│   ├── app.vue                   # Root layout wrapper
│   ├── types/
│   │   └── erp.ts                # Strict TypeScript interfaces matching FastAPI schemas
│   ├── stores/
│   │   ├── erp.ts                # Pinia store for ERP state, Kanban & webhook sync
│   │   └── inventory.ts          # Pinia inventory store export alias
│   └── pages/
│       └── index.vue             # Nuxt UI ERP Admin Dashboard (Inventory + Order Kanban)
├── docker-compose.yml            # Local development stack matching Render container
├── .env.example                  # Documented environment variable template
├── .gitignore                    # Strict git hygiene (.harness/, .env, caches ignored)
├── LICENSE.md                    # Proprietary — All Rights Reserved
└── README.md                     # Architecture & MVP specification
```

---

## 6. Quickstart & Verification

### 6.1 Backend (FastAPI + Pytest)
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
pytest
```

### 6.2 Docker Compose (Render Parity)
```bash
cp .env.example .env
docker compose up --build
```

### 6.3 Frontend (Nuxt 3 + Pinia + Nuxt UI)
```bash
cd frontend
npm install
npm run dev
```

---

## 7. Security, Multi-Tenancy & Compliance Invariants

1. **Tenant Isolation (`DOM-01`):** Every database table and API query is scoped to `tenant_id`.
2. **Secret Hygiene (`SEC-01`):** Never commit `.env`, API keys, or private keys (`*.pem`, `*.key`) to the repository.
3. **Payment & Webhook Verification:** Verify Meta `hub.verify_token` and SePay IPN authorization headers before mutating order state.
