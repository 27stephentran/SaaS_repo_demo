"""Authenticated live-API system readiness and soft-delete retention endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.core.auth import (
    AuthenticatedUser,
    require_live_api,
    resolve_authenticated_tenant,
)
from app.core.config import get_settings, is_production_env
from app.core.database import erp_repository
from app.models.entities import (
    RetentionCleanupRequest,
    RetentionCleanupResponse,
    SystemHealthResponse,
    SystemStackMetadata,
)
from app.services.retention_service import retention_service

router = APIRouter(
    prefix="/api/v1/system",
    tags=["System"],
    dependencies=[Depends(require_live_api)],
)

CurrentUserDep = Annotated[AuthenticatedUser, Depends(require_live_api)]


@router.get("/health", response_model=SystemHealthResponse)
async def system_health(
    _current_user: CurrentUserDep,
) -> SystemHealthResponse | JSONResponse:
    """Authenticated diagnostic readiness endpoint returning database & AI stack metadata."""
    settings = get_settings()
    is_prod = is_production_env()
    db_active = False
    try:
        db_active = await erp_repository.is_postgres_active()
    except Exception:
        db_active = False

    stack_metadata = SystemStackMetadata(
        database="supabase-postgresql-pgvector",
        ai_sdk="google-genai",
        chat_model=settings.gemini_chat_model,
        pro_model=settings.gemini_pro_model,
        embedding_model=settings.gemini_embedding_model,
        meta_api_version=settings.meta_api_version,
        payment_gateway="sepay-vietqr",
        staff_dispatch="telegram-bot-api",
    )

    if is_prod and not db_active:
        unhealthy = SystemHealthResponse(
            status="unhealthy",
            database="unavailable",
            service=settings.app_name,
            version=settings.app_version,
            stack=stack_metadata,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=unhealthy.model_dump(),
        )

    return SystemHealthResponse(
        status="ok",
        database="postgres" if db_active else "fallback",
        service=settings.app_name,
        version=settings.app_version,
        stack=stack_metadata,
    )


@router.post("/retention/cleanup", response_model=RetentionCleanupResponse)
async def run_retention_cleanup(
    current_user: CurrentUserDep,
    payload: Annotated[RetentionCleanupRequest | None, Body()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> RetentionCleanupResponse:
    """Preview or purge expired soft-deleted tenant records past `SOFT_DELETE_RETENTION_DAYS`."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    req = payload if payload is not None else RetentionCleanupRequest()
    if not req.dry_run and current_user.role.strip().lower() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Non-dry-run retention cleanup requires admin role",
        )
    return await retention_service.run_cleanup(
        tenant_id=effective_tenant,
        dry_run=req.dry_run,
        retention_days_override=req.retention_days_override,
    )
