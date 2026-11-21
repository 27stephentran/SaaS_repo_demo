"""Backend-signed HS256 JWT authentication, permission groups, scrypt hashing, and tenant guards."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.database import erp_repository
from app.models.entities import PermissionGroup

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SCRYPT_MAXMEM = 64 * 1024 * 1024

bearer_scheme = HTTPBearer(auto_error=False)


class AuthenticatedUser(BaseModel):
    """Verified API principal extracted from a backend-signed HS256 access token."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_id: str
    role: str
    email: str
    jti: str
    permission_group: PermissionGroup = PermissionGroup.LIVE_API
    username: str | None = None
    full_name: str = ""
    company_id: UUID | None = None
    employee_id: UUID | None = None
    must_change_password: bool = False

    @property
    def sub(self) -> str:
        """Alias `sub` to `user_id` for JWT-claim ergonomics."""
        return self.user_id


def normalize_tenant_id(tenant_id: str) -> str:
    """Validate and normalize a tenant identifier, rejecting empty values."""
    if not isinstance(tenant_id, str):
        raise ValueError("tenant_id must be a non-empty string")
    cleaned = tenant_id.strip()
    if not cleaned:
        raise ValueError("tenant_id must not be empty")
    return cleaned


def resolve_default_permission_group(role: str) -> PermissionGroup:
    """Map an authenticated role to its canonical API permission group."""
    return PermissionGroup.DEMO_API if role.strip().lower() == "demo" else PermissionGroup.LIVE_API


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def hash_password(password: str) -> str:
    """Hash a plaintext password using salted `hashlib.scrypt`."""
    if not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}"
        f"${_b64url_encode(salt)}${_b64url_encode(derived)}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored `scrypt$...` hash in constant time."""
    if not password or not stored_hash:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n_val = int(parts[1])
        r_val = int(parts[2])
        p_val = int(parts[3])
        salt = _b64url_decode(parts[4])
        expected_digest = _b64url_decode(parts[5])
        if n_val <= 1 or r_val <= 0 or p_val <= 0 or not salt or not expected_digest:
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n_val,
            r=r_val,
            p=p_val,
            dklen=len(expected_digest),
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected_digest)


def hash_token(token: str) -> str:
    """Return a SHA-256 hex digest for storing token metadata without plaintext secrets."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sign_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def _create_token(
    *,
    user_id: str | UUID,
    tenant_id: str,
    role: str,
    email: str,
    token_type: str,
    ttl_seconds: int,
    permission_group: PermissionGroup | None = None,
    jti: str | None = None,
    username: str | None = None,
    full_name: str = "",
    company_id: str | UUID | None = None,
    employee_id: str | UUID | None = None,
    must_change_password: bool = False,
) -> str:
    settings = get_settings()
    now = int(time.time())
    norm_tenant = normalize_tenant_id(tenant_id)
    token_jti = jti or str(uuid4())
    effective_group = (
        permission_group
        if permission_group is not None
        else resolve_default_permission_group(role)
    )
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": norm_tenant,
        "role": role,
        "email": email,
        "username": username,
        "full_name": full_name,
        "company_id": str(company_id) if company_id is not None else None,
        "employee_id": str(employee_id) if employee_id is not None else None,
        "must_change_password": bool(must_change_password),
        "permission_group": effective_group.value,
        "type": token_type,
        "jti": token_jti,
        "iss": settings.api_token_issuer,
        "aud": settings.api_token_audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return _sign_jwt(claims, settings.api_token_secret)


def create_access_token(
    user_id: str | UUID,
    tenant_id: str,
    role: str = "staff",
    email: str = "",
    jti: str | None = None,
    ttl_seconds: int | None = None,
    permission_group: PermissionGroup | None = None,
    *,
    username: str | None = None,
    full_name: str = "",
    company_id: str | UUID | None = None,
    employee_id: str | UUID | None = None,
    must_change_password: bool = False,
) -> str:
    """Issue a signed HS256 access JWT bound to `(user_id, tenant_id, role, company_id, permission_group)`."""
    settings = get_settings()
    effective_ttl = (
        ttl_seconds if ttl_seconds is not None else settings.access_token_ttl_seconds
    )
    return _create_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        token_type="access",
        ttl_seconds=effective_ttl,
        permission_group=permission_group,
        jti=jti,
        username=username,
        full_name=full_name,
        company_id=company_id,
        employee_id=employee_id,
        must_change_password=must_change_password,
    )


def create_refresh_token(
    user_id: str | UUID,
    tenant_id: str,
    role: str = "staff",
    email: str = "",
    jti: str | None = None,
    ttl_seconds: int | None = None,
    permission_group: PermissionGroup | None = None,
    *,
    username: str | None = None,
    full_name: str = "",
    company_id: str | UUID | None = None,
    employee_id: str | UUID | None = None,
    must_change_password: bool = False,
) -> str:
    """Issue a signed HS256 refresh JWT bound to `(user_id, tenant_id, role, company_id, permission_group)`."""
    settings = get_settings()
    effective_ttl = (
        ttl_seconds if ttl_seconds is not None else settings.refresh_token_ttl_seconds
    )
    return _create_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        email=email,
        token_type="refresh",
        ttl_seconds=effective_ttl,
        permission_group=permission_group,
        jti=jti,
        username=username,
        full_name=full_name,
        company_id=company_id,
        employee_id=employee_id,
        must_change_password=must_change_password,
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def decode_and_verify_token(
    token: str,
    expected_type: str = "access",
) -> dict[str, Any]:
    """Decode, verify HS256 signature/claims/permission_group, and check JTI revocation status."""
    if not token or not isinstance(token, str):
        raise _unauthorized("Invalid authentication token")

    parts = token.strip().split(".")
    if len(parts) != 3:
        raise _unauthorized("Malformed JWT token")

    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise _unauthorized("Invalid JWT encoding") from exc

    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise _unauthorized("Invalid JWT structure")

    if header.get("alg") != "HS256":
        raise _unauthorized("Unsupported JWT algorithm")

    settings = get_settings()
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = _b64url_encode(
        hmac.new(
            settings.api_token_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
    )
    if not hmac.compare_digest(sig_b64, expected_sig):
        raise _unauthorized("Invalid JWT signature")

    if payload.get("iss") != settings.api_token_issuer:
        raise _unauthorized("Invalid token issuer")
    if payload.get("aud") != settings.api_token_audience:
        raise _unauthorized("Invalid token audience")
    if payload.get("type") != expected_type:
        raise _unauthorized(f"Invalid token type; expected '{expected_type}'")

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= float(exp):
        raise _unauthorized("Token has expired")

    sub = payload.get("sub")
    raw_tenant = payload.get("tenant_id")
    jti = payload.get("jti")
    raw_group = payload.get("permission_group")
    if (
        not isinstance(sub, str)
        or not sub.strip()
        or not isinstance(raw_tenant, str)
        or not raw_tenant.strip()
        or not isinstance(jti, str)
        or not jti.strip()
    ):
        raise _unauthorized("Token missing required subject, tenant, or JTI claims")

    try:
        PermissionGroup(str(raw_group))
    except ValueError as exc:
        raise _unauthorized("Token missing or invalid permission_group claim") from exc

    tenant_id = normalize_tenant_id(raw_tenant)
    if await erp_repository.is_token_revoked(jti=jti, tenant_id=tenant_id):
        raise _unauthorized("Token has been revoked")

    return payload


def _parse_optional_uuid(raw_value: Any) -> UUID | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, UUID):
        return raw_value
    cleaned = str(raw_value).strip()
    if not cleaned:
        return None
    try:
        return UUID(cleaned)
    except ValueError as exc:
        raise _unauthorized("Token contains malformed UUID claim") from exc


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> AuthenticatedUser:
    """FastAPI dependency enforcing valid Bearer access token authentication."""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
    ):
        raise _unauthorized("Authentication credentials were not provided")

    claims = await decode_and_verify_token(
        credentials.credentials, expected_type="access"
    )
    raw_username = claims.get("username")
    username = str(raw_username).strip() if isinstance(raw_username, str) and raw_username.strip() else None
    return AuthenticatedUser(
        user_id=str(claims["sub"]),
        tenant_id=normalize_tenant_id(str(claims["tenant_id"])),
        role=str(claims.get("role", "staff")),
        email=str(claims.get("email", "")),
        jti=str(claims["jti"]),
        permission_group=PermissionGroup(str(claims["permission_group"])),
        username=username,
        full_name=str(claims.get("full_name") or ""),
        company_id=_parse_optional_uuid(claims.get("company_id")),
        employee_id=_parse_optional_uuid(claims.get("employee_id")),
        must_change_password=bool(claims.get("must_change_password", False)),
    )


def require_permission_group(
    required_group: PermissionGroup,
) -> Callable[[AuthenticatedUser], Awaitable[AuthenticatedUser]]:
    """Create a FastAPI dependency enforcing strict API permission group membership."""

    async def _guard(
        current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> AuthenticatedUser:
        if current_user.permission_group != required_group:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Permission group '{current_user.permission_group.value}' is not "
                    f"authorized for '{required_group.value}' endpoints"
                ),
            )
        return current_user

    return _guard


require_live_api_allow_password_change = require_permission_group(PermissionGroup.LIVE_API)
require_demo_api = require_permission_group(PermissionGroup.DEMO_API)


async def require_live_api(
    current_user: Annotated[AuthenticatedUser, Depends(require_live_api_allow_password_change)],
) -> AuthenticatedUser:
    """Enforce `live_api` permission group AND completed first-login password rotation."""
    if current_user.must_change_password:
        user = await erp_repository.get_user_by_id(
            user_id=current_user.user_id,
            tenant_id=current_user.tenant_id,
        )
        if user is None or bool(user.get("must_change_password", False)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Password change required before accessing workspace",
            )
    return current_user


def require_roles(
    *roles: str,
) -> Callable[[AuthenticatedUser], Awaitable[AuthenticatedUser]]:
    """Create a FastAPI dependency enforcing role membership on `live_api` endpoints."""
    allowed = {r.strip().lower() for r in roles if r.strip()}

    async def _role_guard(
        current_user: Annotated[AuthenticatedUser, Depends(require_live_api)],
    ) -> AuthenticatedUser:
        if current_user.role.strip().lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not authorized for this action",
            )
        return current_user

    return _role_guard


def resolve_authenticated_tenant(
    current_user: AuthenticatedUser,
    requested_tenant_id: str | None = None,
    header_tenant_id: str | None = None,
) -> str:
    """Bind tenant scope strictly to the JWT claim, rejecting cross-tenant overrides with 403."""
    jwt_tenant = normalize_tenant_id(current_user.tenant_id)

    for candidate in (requested_tenant_id, header_tenant_id):
        if candidate is None:
            continue
        cleaned = candidate.strip()
        if not cleaned or normalize_tenant_id(cleaned) != jwt_tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant mismatch: request tenant_id does not match authenticated token",
            )

    return jwt_tenant


def resolve_company_scope(
    current_user: AuthenticatedUser,
    requested_company_id: UUID | None = None,
    header_company_id: str | None = None,
    *,
    require_for_admin: bool = False,
) -> UUID | None:
    """Enforce company-level isolation (`manager`/`staff` locked to own `company_id`; `admin` flexible)."""
    parsed_header: UUID | None = None
    if header_company_id is not None and header_company_id.strip():
        try:
            parsed_header = UUID(header_company_id.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid X-Company-Id UUID header",
            ) from exc

    if (
        requested_company_id is not None
        and parsed_header is not None
        and requested_company_id != parsed_header
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company mismatch: conflicting company_id parameters",
        )

    effective_requested = requested_company_id or parsed_header
    role = current_user.role.strip().lower()

    if role in ("manager", "staff"):
        if current_user.company_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is not bound to an active company",
            )
        if effective_requested is not None and effective_requested != current_user.company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Company mismatch: cross-company access is forbidden",
            )
        return current_user.company_id

    if role == "admin":
        if effective_requested is not None:
            return effective_requested
        if require_for_admin:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="company_id is required for this operation",
            )
        return None

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Role '{current_user.role}' is not authorized for company-scoped access",
    )
