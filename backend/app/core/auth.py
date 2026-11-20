"""Backend-signed HS256 JWT authentication, salted scrypt password hashing, and tenant guards."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.database import erp_repository

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

    @property
    def sub(self) -> str:
        """Alias `sub` to `user_id` for JWT-claim ergonomics."""
        return self.user_id


def normalize_tenant_id(tenant_id: str) -> str:
    """Normalize default tenant aliases while rejecting empty tenant identifiers."""
    if not isinstance(tenant_id, str):
        raise ValueError("tenant_id must be a non-empty string")
    cleaned = tenant_id.strip()
    if not cleaned:
        raise ValueError("tenant_id must not be empty")
    if cleaned in ("default-tenant", "tenant-default"):
        return "default-tenant"
    return cleaned


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
    jti: str | None = None,
) -> str:
    settings = get_settings()
    now = int(time.time())
    norm_tenant = normalize_tenant_id(tenant_id)
    token_jti = jti or str(uuid4())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": norm_tenant,
        "role": role,
        "email": email,
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
) -> str:
    """Issue a signed HS256 access JWT bound to `(user_id, tenant_id, role)`."""
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
        jti=jti,
    )


def create_refresh_token(
    user_id: str | UUID,
    tenant_id: str,
    role: str = "staff",
    email: str = "",
    jti: str | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Issue a signed HS256 refresh JWT bound to `(user_id, tenant_id, role)`."""
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
        jti=jti,
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
    """Decode, verify HS256 signature/claims, and check JTI revocation status."""
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
    if (
        not isinstance(sub, str)
        or not sub.strip()
        or not isinstance(raw_tenant, str)
        or not raw_tenant.strip()
        or not isinstance(jti, str)
        or not jti.strip()
    ):
        raise _unauthorized("Token missing required subject, tenant, or JTI claims")

    tenant_id = normalize_tenant_id(raw_tenant)
    if await erp_repository.is_token_revoked(jti=jti, tenant_id=tenant_id):
        raise _unauthorized("Token has been revoked")

    return payload


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
    return AuthenticatedUser(
        user_id=str(claims["sub"]),
        tenant_id=normalize_tenant_id(str(claims["tenant_id"])),
        role=str(claims.get("role", "staff")),
        email=str(claims.get("email", "")),
        jti=str(claims["jti"]),
    )


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
