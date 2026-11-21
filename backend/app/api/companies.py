"""Multi-Company management, Vietnam bank catalog, SePay Bank Hub linking, and Manager provisioning endpoints."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from app.core.auth import (
    AuthenticatedUser,
    hash_password,
    require_live_api,
    require_roles,
    resolve_authenticated_tenant,
    resolve_company_scope,
)
from app.core.database import erp_repository
from app.models.entities import (
    BankCatalogResponse,
    BankDirectoryEntry,
    BankHubLinkTokenRequest,
    BankHubLinkTokenResponse,
    CompanyBankUpdateRequest,
    CompanyCreateRequest,
    CompanyResponse,
    ManagerCreateRequest,
    ManagerCreateResponse,
    ManagerSummaryResponse,
    RestoreResponse,
    SePayLinkedBankAccount,
    SoftDeleteResponse,
)
from app.services.integrations import (
    VIETNAM_BANKS_DIRECTORY,
    sepay_bankhub_client,
    sepay_client,
)

router = APIRouter(
    prefix="/api/v1/companies",
    tags=["Companies"],
    dependencies=[Depends(require_live_api)],
)

CurrentUserDep = Annotated[AuthenticatedUser, Depends(require_live_api)]
AdminOnlyDep = Annotated[AuthenticatedUser, Depends(require_roles("admin"))]
AdminOrManagerDep = Annotated[AuthenticatedUser, Depends(require_roles("admin", "manager"))]


@router.get("/banks", response_model=BankCatalogResponse)
async def get_bank_catalog(
    _current_user: AdminOrManagerDep,
) -> BankCatalogResponse:
    """Return curated Vietnam bank directory and SePay-linked bank accounts when API key is set."""
    banks = [BankDirectoryEntry.model_validate(entry) for entry in VIETNAM_BANKS_DIRECTORY]
    raw_linked = await sepay_client.list_linked_bank_accounts()
    linked_accounts = [SePayLinkedBankAccount.model_validate(item) for item in raw_linked]
    return BankCatalogResponse(
        banks=banks,
        sepay_linked_accounts=linked_accounts,
        sepay_api_configured=bool((sepay_client.api_key or "").strip()),
        bankhub_configured=sepay_bankhub_client.is_configured,
    )


@router.get("", response_model=list[CompanyResponse])
async def list_companies(
    current_user: CurrentUserDep,
    include_inactive: bool = Query(default=False),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[CompanyResponse]:
    """List companies visible to the authenticated user (`admin` sees all; `manager`/`staff` see own company)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    role = current_user.role.strip().lower()
    if role == "admin":
        return await erp_repository.list_companies(
            tenant_id=effective_tenant,
            include_inactive=include_inactive,
        )
    if current_user.company_id is None:
        return []
    return await erp_repository.list_companies(
        tenant_id=effective_tenant,
        include_inactive=False,
        company_id=current_user.company_id,
    )


@router.post(
    "",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company(
    payload: CompanyCreateRequest,
    current_user: AdminOnlyDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> CompanyResponse:
    """Create a new subsidiary/branch company with SePay banking details (`admin` only)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    bank_name = payload.bank_name.strip()
    if not bank_name:
        matched_bank = next(
            (
                b
                for b in VIETNAM_BANKS_DIRECTORY
                if str(b["code"]).upper() == payload.bank_code.strip().upper()
            ),
            None,
        )
        bank_name = str(matched_bank["short_name"]) if matched_bank else payload.bank_code.strip().upper()

    try:
        return await erp_repository.create_company(
            tenant_id=effective_tenant,
            name=payload.name,
            code=payload.code,
            bank_code=payload.bank_code,
            bank_name=bank_name,
            bank_account_number=payload.bank_account_number,
            bank_account_name=payload.bank_account_name,
            sepay_sub_account=payload.sepay_sub_account,
            sepay_memo_prefix=payload.sepay_memo_prefix,
            sepay_bank_account_id=payload.sepay_bank_account_id,
            meta_page_id=payload.meta_page_id,
        )
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc) if isinstance(exc, ValueError) else "Company code or bank channel already exists",
        ) from exc


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID,
    current_user: CurrentUserDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> CompanyResponse:
    """Retrieve a single company within the user's authorized scope."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    scoped_company_id = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
    )
    target_id = scoped_company_id or company_id
    company = await erp_repository.get_company(
        company_id=target_id,
        tenant_id=effective_tenant,
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )
    return company


@router.patch("/{company_id}/bank", response_model=CompanyResponse)
@router.put("/{company_id}/bank", response_model=CompanyResponse, include_in_schema=False)
async def update_company_bank(
    company_id: UUID,
    payload: CompanyBankUpdateRequest,
    current_user: AdminOnlyDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> CompanyResponse:
    """Update a company's SePay banking and VietQR details (`admin` only)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    scoped_company_id = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
    )
    target_id = scoped_company_id or company_id

    bank_name = payload.bank_name.strip()
    if not bank_name:
        matched_bank = next(
            (
                b
                for b in VIETNAM_BANKS_DIRECTORY
                if str(b["code"]).upper() == payload.bank_code.strip().upper()
            ),
            None,
        )
        bank_name = str(matched_bank["short_name"]) if matched_bank else payload.bank_code.strip().upper()

    try:
        updated = await erp_repository.update_company_bank(
            company_id=target_id,
            tenant_id=effective_tenant,
            bank_code=payload.bank_code,
            bank_name=bank_name,
            bank_account_number=payload.bank_account_number,
            bank_account_name=payload.bank_account_name,
            sepay_sub_account=payload.sepay_sub_account,
            sepay_memo_prefix=payload.sepay_memo_prefix,
            sepay_bank_account_id=payload.sepay_bank_account_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )
    return updated


@router.post("/{company_id}/sepay-bankhub-link", response_model=BankHubLinkTokenResponse)
async def create_sepay_bankhub_link(
    company_id: UUID,
    current_user: AdminOnlyDep,
    payload: Annotated[BankHubLinkTokenRequest | None, Body()] = None,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> BankHubLinkTokenResponse:
    """Provision a SePay Bank Hub company XID (if needed) and return a link token (`admin` only)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    scoped_company_id = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
    )
    target_id = scoped_company_id or company_id

    company = await erp_repository.get_company(
        company_id=target_id,
        tenant_id=effective_tenant,
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )

    if not sepay_bankhub_client.is_configured:
        return BankHubLinkTokenResponse(
            company_id=company.id,
            configured=False,
            sepay_bankhub_company_xid=company.sepay_bankhub_company_xid,
            link_token=None,
            connect_url=None,
            expires_at=None,
            detail="SePay Bank Hub credentials are not configured",
        )

    company_xid = company.sepay_bankhub_company_xid
    if not company_xid:
        created_bh = await sepay_bankhub_client.create_company(
            name=company.name,
            code=company.code,
        )
        company_xid = created_bh.get("xid")
        if not company_xid:
            return BankHubLinkTokenResponse(
                company_id=company.id,
                configured=True,
                sepay_bankhub_company_xid=None,
                link_token=None,
                connect_url=None,
                expires_at=None,
                detail=str(created_bh.get("detail") or created_bh.get("error") or "Unable to register Bank Hub company"),
            )
        await erp_repository.update_company_bankhub_xid(
            company_id=company.id,
            tenant_id=effective_tenant,
            xid=company_xid,
        )

    token_result = await sepay_bankhub_client.create_link_token(
        company_xid=company_xid,
        bank_code=(payload.bank_code if payload and payload.bank_code else company.bank_code),
        redirect_url=payload.redirect_url if payload else None,
    )
    return BankHubLinkTokenResponse(
        company_id=company.id,
        configured=True,
        sepay_bankhub_company_xid=company_xid,
        link_token=token_result.get("link_token"),
        connect_url=token_result.get("connect_url"),
        expires_at=token_result.get("expires_at"),
        detail=str(token_result.get("detail") or ""),
    )


@router.delete("/{company_id}", response_model=SoftDeleteResponse)
async def soft_delete_company(
    company_id: UUID,
    current_user: AdminOnlyDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> SoftDeleteResponse:
    """Soft-delete a company (`admin` only)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    deleted = await erp_repository.soft_delete_company(
        company_id=company_id,
        tenant_id=effective_tenant,
    )
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )
    return SoftDeleteResponse(
        resource="companies",
        record_id=deleted.id,
        tenant_id=deleted.tenant_id,
        is_active=deleted.is_active,
        deactivated_at=deleted.deactivated_at or datetime.now(UTC),
    )


@router.post("/{company_id}/restore", response_model=RestoreResponse)
async def restore_company(
    company_id: UUID,
    current_user: AdminOnlyDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> RestoreResponse:
    """Restore a soft-deleted company (`admin` only)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    restored = await erp_repository.restore_company(
        company_id=company_id,
        tenant_id=effective_tenant,
    )
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )
    return RestoreResponse(
        resource="companies",
        record_id=restored.id,
        tenant_id=restored.tenant_id,
        is_active=restored.is_active,
        deactivated_at=restored.deactivated_at,
    )


@router.get("/{company_id}/managers", response_model=list[ManagerSummaryResponse])
async def list_company_managers(
    company_id: UUID,
    current_user: AdminOrManagerDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> list[ManagerSummaryResponse]:
    """List manager accounts assigned to a company (`admin` or owning `manager`)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    scoped_company_id = resolve_company_scope(
        current_user,
        requested_company_id=company_id,
    )
    target_id = scoped_company_id or company_id
    company = await erp_repository.get_company(
        company_id=target_id,
        tenant_id=effective_tenant,
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )
    rows = await erp_repository.list_company_managers(
        tenant_id=effective_tenant,
        company_id=company.id,
    )
    return [ManagerSummaryResponse.model_validate(row) for row in rows]


@router.post(
    "/{company_id}/managers",
    response_model=ManagerCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_manager(
    company_id: UUID,
    payload: ManagerCreateRequest,
    current_user: AdminOnlyDep,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> ManagerCreateResponse:
    """Provision a company Manager login account with `must_change_password=True` (`admin` only)."""
    effective_tenant = resolve_authenticated_tenant(
        current_user,
        header_tenant_id=x_tenant_id,
    )
    company = await erp_repository.get_company(
        company_id=company_id,
        tenant_id=effective_tenant,
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )

    username = (
        payload.username.strip()
        if payload.username and payload.username.strip()
        else f"mgr_{company.code.lower()}_{secrets.token_hex(3)}"
    )
    email = (
        payload.email.strip().lower()
        if payload.email and payload.email.strip()
        else f"{username.lower()}@{company.code.lower()}.local"
    )
    temporary_password = (
        payload.password
        if payload.password
        else secrets.token_urlsafe(12)
    )

    try:
        user = await erp_repository.create_user(
            email=email,
            password_hash=hash_password(temporary_password),
            role="manager",
            tenant_id=effective_tenant,
            is_active=True,
            username=username,
            full_name=payload.full_name,
            company_id=company.id,
            must_change_password=True,
        )
    except (ValueError, IntegrityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email or username already exists for the tenant",
        ) from exc

    return ManagerCreateResponse(
        user_id=UUID(str(user["id"])),
        tenant_id=effective_tenant,
        company_id=company.id,
        company_name=company.name,
        email=str(user["email"]),
        username=str(user.get("username") or username),
        full_name=str(user.get("full_name") or payload.full_name),
        role="manager",
        temporary_password=temporary_password,
        must_change_password=True,
        created_at=user["created_at"],
    )
