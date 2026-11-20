"""FastAPI application entrypoint for the Omnichannel AI Sales & ERP B2B SaaS Backend."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import compat_router as auth_compat_router
from app.api.auth import router as auth_router
from app.api.erp import router as erp_router
from app.api.webhooks import router as webhooks_router
from app.core.config import get_settings
from app.core.database import erp_repository
from app.services.integrations import close_integration_clients

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and clean shutdown of pooled integration HTTP clients."""
    yield
    await close_integration_clients()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Omnichannel AI Sales & ERP B2B SaaS Backend powered by FastAPI, "
        "Supabase PostgreSQL + pgvector (`<=>`), Google Gemini (`google-genai`), "
        "Meta Graph API v18.0+, SePay VietQR IPN, and Telegram Bot API."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-Id", "X-CSRF-Token"],
)

app.include_router(auth_router)
app.include_router(auth_compat_router)
app.include_router(erp_router)
app.include_router(webhooks_router)


@app.get("/health", tags=["System"])
async def health_check() -> JSONResponse:
    """Liveness and deep database readiness probe for Render & Docker Compose."""
    is_prod = os.getenv("APP_ENV", settings.app_env).strip().lower() == "production"
    db_active = False
    try:
        db_active = await erp_repository.is_postgres_active()
    except Exception:
        db_active = False

    stack_metadata = {
        "database": "supabase-postgresql-pgvector",
        "ai_sdk": "google-genai",
        "chat_model": settings.gemini_chat_model,
        "pro_model": settings.gemini_pro_model,
        "embedding_model": settings.gemini_embedding_model,
        "meta_api_version": settings.meta_api_version,
        "payment_gateway": "sepay-vietqr",
        "staff_dispatch": "telegram-bot-api",
    }

    if is_prod and not db_active:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "database": "unavailable",
                "service": settings.app_name,
                "version": settings.app_version,
                "stack": stack_metadata,
            },
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "ok",
            "database": "postgres" if db_active else "fallback",
            "service": settings.app_name,
            "version": settings.app_version,
            "stack": stack_metadata,
        },
    )
