"""FastAPI application entrypoint for the Omnichannel AI Sales & ERP B2B SaaS Backend."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import compat_router as auth_compat_router
from app.api.auth import router as auth_router
from app.api.companies import router as companies_router
from app.api.demo import router as demo_router
from app.api.erp import router as erp_router
from app.api.system import router as system_router
from app.api.webhooks import router as webhooks_router
from app.core.config import get_settings, is_production_env
from app.core.database import erp_repository
from app.models.entities import HealthCheckResponse
from app.services.integrations import close_integration_clients

settings = get_settings()
_is_prod_startup = is_production_env()


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
    docs_url=None if _is_prod_startup else "/docs",
    redoc_url=None if _is_prod_startup else "/redoc",
    openapi_url=None if _is_prod_startup else "/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-Id", "X-Company-Id", "X-CSRF-Token"],
)

app.include_router(auth_router)
app.include_router(auth_compat_router)
app.include_router(companies_router)
app.include_router(erp_router)
app.include_router(demo_router)
app.include_router(system_router)
app.include_router(webhooks_router)


@app.get("/health", response_model=HealthCheckResponse, tags=["System"])
async def health_check() -> HealthCheckResponse | JSONResponse:
    """Minimal liveness/readiness probe returning only `{status}` without leaking stack metadata."""
    is_prod = is_production_env()
    db_active = False
    try:
        db_active = await erp_repository.is_postgres_active()
    except Exception:
        db_active = False

    if is_prod and not db_active:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=HealthCheckResponse(status="unhealthy").model_dump(),
        )

    return HealthCheckResponse(status="ok")
