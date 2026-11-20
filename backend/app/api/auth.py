"""Authentication endpoints for tenant-scoped HS256 JWT issuance, rotation, logout, and identity."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from app.core.auth import (
    AuthenticatedUser,
    create_access_token,
    create_refresh_token,
    decode_and_verify_token,
    get_current_user,
    hash_token,
    normalize_tenant_id,
    verify_password,
)
from app.core.config import get_settings
from app.core.database import erp_repository
from app.models.entities import (
    AuthenticatedPrincipalResponse,
    TokenIssueRequest,
    TokenLogoutRequest,
    TokenPairResponse,
    TokenRefreshRequest,
)


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter keyed by client IP and account identity."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def reset(self) -> None:
        """Clear recorded rate-limit timestamps (for test isolation)."""
        self._events.clear()

    def check_and_record(self, keys: list[str], limit: int) -> int | None:
        """Return Retry-After seconds if any key exceeds `limit`, otherwise record the attempt."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        for key in keys:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])) + 1)
                return retry_after

        for key in keys:
            self._events[key].append(now)
        return None


auth_rate_limiter = SlidingWindowRateLimiter(window_seconds=60.0)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
compat_router = APIRouter(prefix="/auth", tags=["Auth"], include_in_schema=False)


async def _issue_token_pair(
    *,
    user_id: str,
    tenant_id: str,
    role: str,
    email: str,
) -> TokenPairResponse:
    settings = get_settings()
    now_dt = datetime.now(UTC)

    access_jti = str(uuid4())
    refresh_jti = str(uuid4())

    access_token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        jti=access_jti,
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    refresh_token = create_refresh_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        jti=refresh_jti,
        ttl_seconds=settings.refresh_token_ttl_seconds,
    )

    await erp_repository.store_token_record(
        user_id=user_id,
        jti=access_jti,
        token_hash=hash_token(access_token),
        token_type="access",
        expires_at=now_dt + timedelta(seconds=settings.access_token_ttl_seconds),
        tenant_id=tenant_id,
    )
    await erp_repository.store_token_record(
        user_id=user_id,
        jti=refresh_jti,
        token_hash=hash_token(refresh_token),
        token_type="refresh",
        expires_at=now_dt + timedelta(seconds=settings.refresh_token_ttl_seconds),
        tenant_id=tenant_id,
    )

    return TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.access_token_ttl_seconds,
        tenant_id=tenant_id,
        role=role,
    )


@router.post("/token", response_model=TokenPairResponse)
@compat_router.post("/token", response_model=TokenPairResponse, include_in_schema=False)
async def issue_token(
    request: Request,
    payload: TokenIssueRequest,
) -> TokenPairResponse:
    """Authenticate tenant-scoped user credentials and issue an access/refresh JWT pair."""
    settings = get_settings()
    try:
        norm_tenant = normalize_tenant_id(payload.tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    norm_email = payload.email.strip().lower()
    client_ip = request.client.host if request.client else "unknown"
    retry_after = auth_rate_limiter.check_and_record(
        keys=[
            f"ip:{client_ip}",
            f"acct:{norm_tenant}:{norm_email}",
        ],
        limit=settings.auth_rate_limit_per_minute,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts; please retry later",
            headers={"Retry-After": str(retry_after)},
        )

    user = await erp_repository.get_user_by_email(email=norm_email, tenant_id=norm_tenant)
    if (
        user is None
        or not bool(user.get("is_active", True))
        or not verify_password(payload.password, str(user.get("password_hash", "")))
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email, password, or tenant credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await _issue_token_pair(
        user_id=str(user["id"]),
        tenant_id=norm_tenant,
        role=str(user.get("role", "staff")),
        email=str(user.get("email", norm_email)),
    )


@router.post("/refresh", response_model=TokenPairResponse)
@compat_router.post("/refresh", response_model=TokenPairResponse, include_in_schema=False)
async def refresh_token_pair(
    request: Request,
    payload: TokenRefreshRequest,
) -> TokenPairResponse:
    """Rotate a valid non-revoked refresh token into a fresh access/refresh token pair."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    retry_after = auth_rate_limiter.check_and_record(
        keys=[f"ip:{client_ip}"],
        limit=settings.auth_rate_limit_per_minute,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts; please retry later",
            headers={"Retry-After": str(retry_after)},
        )

    claims = await decode_and_verify_token(payload.refresh_token, expected_type="refresh")
    tenant_id = normalize_tenant_id(str(claims["tenant_id"]))
    user_id = str(claims["sub"])
    role = str(claims.get("role", "staff"))
    email = str(claims.get("email", ""))
    old_jti = str(claims["jti"])

    if email:
        user = await erp_repository.get_user_by_email(email=email, tenant_id=tenant_id)
        if user is not None and not bool(user.get("is_active", True)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive",
                headers={"WWW-Authenticate": "Bearer"},
            )

    revoked = await erp_repository.revoke_token_jti(jti=old_jti, tenant_id=tenant_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has already been used or revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await _issue_token_pair(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
    )


@router.post("/logout")
@compat_router.post("/logout", include_in_schema=False)
async def logout(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    payload: Annotated[TokenLogoutRequest | None, Body()] = None,
) -> dict[str, Any]:
    """Revoke the caller's active access JTI and optional refresh token JTI."""
    await erp_repository.revoke_token_jti(
        jti=current_user.jti,
        tenant_id=current_user.tenant_id,
    )

    if payload is not None and payload.refresh_token:
        refresh_claims = await decode_and_verify_token(
            payload.refresh_token, expected_type="refresh"
        )
        refresh_tenant = normalize_tenant_id(str(refresh_claims["tenant_id"]))
        if (
            refresh_tenant != current_user.tenant_id
            or str(refresh_claims["sub"]) != current_user.user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Refresh token does not belong to the authenticated principal",
            )
        await erp_repository.revoke_token_jti(
            jti=str(refresh_claims["jti"]),
            tenant_id=current_user.tenant_id,
        )

    return {"status": "logged_out", "revoked": True}


@router.get("/me", response_model=AuthenticatedPrincipalResponse)
@compat_router.get("/me", response_model=AuthenticatedPrincipalResponse, include_in_schema=False)
async def get_authenticated_me(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedPrincipalResponse:
    """Return the authenticated principal's verified identity claims."""
    return AuthenticatedPrincipalResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        tenant_id=current_user.tenant_id,
        role=current_user.role,
    )
