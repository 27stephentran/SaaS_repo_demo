"""Authentication endpoints for tenant-scoped HS256 JWT issuance, signup, demo-signin, rotation, logout, and identity."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.core.auth import (
    AuthenticatedUser,
    create_access_token,
    create_refresh_token,
    decode_and_verify_token,
    get_current_user,
    hash_password,
    hash_token,
    normalize_tenant_id,
    require_live_api_allow_password_change,
    resolve_default_permission_group,
    verify_password,
)
from app.core.config import get_settings, is_production_env
from app.core.database import erp_repository
from app.models.entities import (
    AuthenticatedPrincipalResponse,
    ChangePasswordRequest,
    LogoutResponse,
    PermissionGroup,
    SignupRequest,
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
    permission_group: PermissionGroup | None = None,
    access_ttl_seconds: int | None = None,
    refresh_ttl_seconds: int | None = None,
    username: str | None = None,
    full_name: str = "",
    company_id: UUID | str | None = None,
    employee_id: UUID | str | None = None,
    must_change_password: bool = False,
) -> TokenPairResponse:
    settings = get_settings()
    now_dt = datetime.now(UTC)

    effective_group = (
        permission_group
        if permission_group is not None
        else resolve_default_permission_group(role)
    )
    effective_access_ttl = (
        access_ttl_seconds
        if access_ttl_seconds is not None
        else settings.access_token_ttl_seconds
    )
    effective_refresh_ttl = (
        refresh_ttl_seconds
        if refresh_ttl_seconds is not None
        else settings.refresh_token_ttl_seconds
    )

    comp_uuid = (
        company_id
        if isinstance(company_id, UUID)
        else (UUID(str(company_id)) if company_id else None)
    )
    emp_uuid = (
        employee_id
        if isinstance(employee_id, UUID)
        else (UUID(str(employee_id)) if employee_id else None)
    )

    access_jti = str(uuid4())
    refresh_jti = str(uuid4())

    access_token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        jti=access_jti,
        ttl_seconds=effective_access_ttl,
        permission_group=effective_group,
        username=username,
        full_name=full_name,
        company_id=comp_uuid,
        employee_id=emp_uuid,
        must_change_password=must_change_password,
    )
    refresh_token = create_refresh_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        jti=refresh_jti,
        ttl_seconds=effective_refresh_ttl,
        permission_group=effective_group,
        username=username,
        full_name=full_name,
        company_id=comp_uuid,
        employee_id=emp_uuid,
        must_change_password=must_change_password,
    )

    await erp_repository.store_token_record(
        user_id=user_id,
        jti=access_jti,
        token_hash=hash_token(access_token),
        token_type="access",
        expires_at=now_dt + timedelta(seconds=effective_access_ttl),
        tenant_id=tenant_id,
    )
    await erp_repository.store_token_record(
        user_id=user_id,
        jti=refresh_jti,
        token_hash=hash_token(refresh_token),
        token_type="refresh",
        expires_at=now_dt + timedelta(seconds=effective_refresh_ttl),
        tenant_id=tenant_id,
    )

    return TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=effective_access_ttl,
        tenant_id=tenant_id,
        role=role,
        permission_group=effective_group,
        user_id=str(user_id),
        username=username,
        full_name=full_name,
        company_id=comp_uuid,
        employee_id=emp_uuid,
        must_change_password=bool(must_change_password),
    )


async def _authenticate_live_user(
    request: Request,
    payload: TokenIssueRequest,
) -> TokenPairResponse:
    settings = get_settings()
    try:
        norm_tenant = normalize_tenant_id(payload.tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    norm_ident = payload.resolved_identifier.lower()
    client_ip = request.client.host if request.client else "unknown"
    retry_after = auth_rate_limiter.check_and_record(
        keys=[
            f"ip:{client_ip}",
            f"acct:{norm_tenant}:{norm_ident}",
        ],
        limit=settings.auth_rate_limit_per_minute,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts; please retry later",
            headers={"Retry-After": str(retry_after)},
        )

    user = await erp_repository.get_user_by_identifier(
        identifier=norm_ident,
        tenant_id=norm_tenant,
    )
    if (
        user is None
        or not bool(user.get("is_active", True))
        or not verify_password(payload.password, str(user.get("password_hash", "")))
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username, password, or tenant credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_role = str(user.get("role", "staff")).strip()
    if user_role.lower() == "demo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo accounts cannot access live_api sign-in; use /api/v1/auth/demo-signin",
        )

    return await _issue_token_pair(
        user_id=str(user["id"]),
        tenant_id=norm_tenant,
        role=user_role,
        email=str(user.get("email", norm_ident)),
        permission_group=PermissionGroup.LIVE_API,
        username=user.get("username"),
        full_name=str(user.get("full_name") or ""),
        company_id=user.get("company_id"),
        employee_id=user.get("employee_id"),
        must_change_password=bool(user.get("must_change_password", False)),
    )


@router.post("/token", response_model=TokenPairResponse)
@compat_router.post("/token", response_model=TokenPairResponse, include_in_schema=False)
async def issue_token(
    request: Request,
    payload: TokenIssueRequest,
) -> TokenPairResponse:
    """Authenticate tenant-scoped live operator credentials and issue a `live_api` JWT pair."""
    return await _authenticate_live_user(request=request, payload=payload)


@router.post("/signin", response_model=TokenPairResponse)
@compat_router.post("/signin", response_model=TokenPairResponse, include_in_schema=False)
async def signin(
    request: Request,
    payload: TokenIssueRequest,
) -> TokenPairResponse:
    """Sign in a live tenant operator and issue a `live_api` JWT pair."""
    return await _authenticate_live_user(request=request, payload=payload)


@router.post("/login", response_model=TokenPairResponse)
@compat_router.post("/login", response_model=TokenPairResponse, include_in_schema=False)
async def login(
    request: Request,
    payload: TokenIssueRequest,
) -> TokenPairResponse:
    """Alias for live tenant operator sign-in issuing a `live_api` JWT pair."""
    return await _authenticate_live_user(request=request, payload=payload)


@router.post(
    "/signup",
    response_model=TokenPairResponse,
    status_code=status.HTTP_201_CREATED,
)
@compat_router.post(
    "/signup",
    response_model=TokenPairResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def signup(
    request: Request,
    payload: SignupRequest,
) -> TokenPairResponse:
    """Register a founding `admin` account (`live_api`) on a brand-new tenant."""
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
            f"signup:{norm_tenant}:{norm_email}",
        ],
        limit=settings.auth_rate_limit_per_minute,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many signup attempts; please retry later",
            headers={"Retry-After": str(retry_after)},
        )

    demo_tenant = settings.demo_tenant_id.strip()
    if demo_tenant and norm_tenant == demo_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot self-register on the demo sandbox tenant",
        )

    if await erp_repository.tenant_has_users(norm_tenant):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant already exists; new users must be invited by a tenant administrator",
        )

    try:
        user = await erp_repository.create_user(
            email=norm_email,
            password_hash=hash_password(payload.password),
            role="admin",
            tenant_id=norm_tenant,
            is_active=True,
            full_name=payload.full_name,
        )
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists for the tenant",
        ) from exc

    return await _issue_token_pair(
        user_id=str(user["id"]),
        tenant_id=norm_tenant,
        role="admin",
        email=str(user.get("email", norm_email)),
        permission_group=PermissionGroup.LIVE_API,
        full_name=str(user.get("full_name") or ""),
    )


@router.post("/demo-signin", response_model=TokenPairResponse)
@compat_router.post("/demo-signin", response_model=TokenPairResponse, include_in_schema=False)
async def demo_signin(request: Request) -> TokenPairResponse:
    """Issue a short-lived `demo_api` token pair (`role="demo"`) for sandbox preview."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    retry_after = auth_rate_limiter.check_and_record(
        keys=[f"ip:{client_ip}", "demo-signin"],
        limit=settings.auth_rate_limit_per_minute,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many demo sign-in attempts; please retry later",
            headers={"Retry-After": str(retry_after)},
        )

    demo_tenant = settings.demo_tenant_id.strip() or settings.default_tenant_id.strip()
    if not demo_tenant:
        if is_production_env():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Demo sandbox tenant is not configured",
            )
        demo_tenant = "demo-sandbox"

    demo_email = f"demo-sandbox@{demo_tenant}.local"
    demo_user = await erp_repository.get_user_by_email(email=demo_email, tenant_id=demo_tenant)
    if demo_user is None:
        try:
            demo_user = await erp_repository.create_user(
                email=demo_email,
                password_hash=hash_password(secrets.token_urlsafe(24)),
                role="demo",
                tenant_id=demo_tenant,
                is_active=True,
            )
        except (ValueError, IntegrityError):
            demo_user = await erp_repository.get_user_by_email(
                email=demo_email, tenant_id=demo_tenant
            )

    if demo_user is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to provision demo sandbox session",
        )

    demo_ttl = settings.demo_token_ttl_seconds
    return await _issue_token_pair(
        user_id=str(demo_user["id"]),
        tenant_id=demo_tenant,
        role="demo",
        email=str(demo_user.get("email", demo_email)),
        permission_group=PermissionGroup.DEMO_API,
        access_ttl_seconds=demo_ttl,
        refresh_ttl_seconds=demo_ttl,
    )


@router.post("/refresh", response_model=TokenPairResponse)
@compat_router.post("/refresh", response_model=TokenPairResponse, include_in_schema=False)
async def refresh_token_pair(
    request: Request,
    payload: TokenRefreshRequest,
) -> TokenPairResponse:
    """Rotate a valid non-revoked refresh token while preserving `permission_group`."""
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
    perm_group = PermissionGroup(str(claims["permission_group"]))

    username = claims.get("username")
    full_name = str(claims.get("full_name") or "")
    company_id = claims.get("company_id")
    employee_id = claims.get("employee_id")
    must_change_password = bool(claims.get("must_change_password", False))

    user = await erp_repository.get_user_by_id(user_id=user_id, tenant_id=tenant_id)
    if user is None and email:
        user = await erp_repository.get_user_by_email(email=email, tenant_id=tenant_id)
    if user is not None:
        if not bool(user.get("is_active", True)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token_iat = claims.get("iat")
        pwd_changed_at = user.get("password_changed_at")
        if pwd_changed_at is not None and isinstance(token_iat, (int, float)):
            changed_dt = (
                pwd_changed_at
                if pwd_changed_at.tzinfo is not None
                else pwd_changed_at.replace(tzinfo=UTC)
            )
            if int(token_iat) < int(changed_dt.timestamp()):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Refresh token invalidated by password change",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        if bool(claims.get("must_change_password", False)) and not bool(
            user.get("must_change_password", False)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token invalidated by password change",
                headers={"WWW-Authenticate": "Bearer"},
            )
        role = str(user.get("role", role))
        email = str(user.get("email", email))
        username = user.get("username")
        full_name = str(user.get("full_name") or "")
        company_id = user.get("company_id")
        employee_id = user.get("employee_id")
        must_change_password = bool(user.get("must_change_password", False))

    revoked = await erp_repository.revoke_token_jti(jti=old_jti, tenant_id=tenant_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has already been used or revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    is_demo = perm_group == PermissionGroup.DEMO_API
    return await _issue_token_pair(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        permission_group=perm_group,
        access_ttl_seconds=settings.demo_token_ttl_seconds if is_demo else None,
        refresh_ttl_seconds=settings.demo_token_ttl_seconds if is_demo else None,
        username=username,
        full_name=full_name,
        company_id=company_id,
        employee_id=employee_id,
        must_change_password=must_change_password,
    )


@router.post("/change-password", response_model=TokenPairResponse)
@compat_router.post("/change-password", response_model=TokenPairResponse, include_in_schema=False)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: Annotated[AuthenticatedUser, Depends(require_live_api_allow_password_change)],
) -> TokenPairResponse:
    """Rotate the authenticated live user's password and clear `must_change_password`."""
    user = await erp_repository.get_user_by_id(
        user_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
    )
    if user is None or not bool(user.get("is_active", True)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(payload.current_password, str(user.get("password_hash", ""))):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )

    updated = await erp_repository.update_user_password(
        user_id=current_user.user_id,
        password_hash=hash_password(payload.new_password),
        tenant_id=current_user.tenant_id,
        must_change_password=False,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found",
        )

    await erp_repository.revoke_all_user_tokens(
        user_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
    )

    return await _issue_token_pair(
        user_id=str(updated["id"]),
        tenant_id=current_user.tenant_id,
        role=str(updated.get("role", current_user.role)),
        email=str(updated.get("email", current_user.email)),
        permission_group=PermissionGroup.LIVE_API,
        username=updated.get("username"),
        full_name=str(updated.get("full_name") or ""),
        company_id=updated.get("company_id"),
        employee_id=updated.get("employee_id"),
        must_change_password=False,
    )


@router.post("/logout", response_model=LogoutResponse)
@compat_router.post("/logout", response_model=LogoutResponse, include_in_schema=False)
async def logout(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    payload: Annotated[TokenLogoutRequest | None, Body()] = None,
) -> LogoutResponse:
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

    return LogoutResponse(status="logged_out", revoked=True)


@router.get("/me", response_model=AuthenticatedPrincipalResponse)
@compat_router.get("/me", response_model=AuthenticatedPrincipalResponse, include_in_schema=False)
async def get_authenticated_me(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedPrincipalResponse:
    """Return the authenticated principal's verified identity claims."""
    must_change = current_user.must_change_password
    if must_change:
        user = await erp_repository.get_user_by_id(
            user_id=current_user.user_id,
            tenant_id=current_user.tenant_id,
        )
        if user is not None:
            must_change = bool(user.get("must_change_password", False))

    return AuthenticatedPrincipalResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        tenant_id=current_user.tenant_id,
        role=current_user.role,
        permission_group=current_user.permission_group,
        username=current_user.username,
        full_name=current_user.full_name,
        company_id=current_user.company_id,
        employee_id=current_user.employee_id,
        must_change_password=must_change,
    )
