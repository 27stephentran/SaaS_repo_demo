"""Comprehensive integration & regression tests for Multi-Company, RBAC, Staff Onboarding, SePay routing, and Omnichannel Chats."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

import tests.conftest as test_conftest
from app.core.auth import (
    AuthenticatedUser,
    create_access_token,
    hash_token,
    resolve_company_scope,
)
from app.core.database import erp_repository
from app.main import app
from app.models.entities import PermissionGroup
from app.services.integrations import sepay_bankhub_client, sepay_client


async def _signin(
    client: httpx.AsyncClient,
    *,
    identifier: str,
    password: str,
    tenant_id: str = "default-tenant",
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/signin",
        json={
            "identifier": identifier,
            "password": password,
            "tenant_id": tenant_id,
        },
    )


async def _create_company(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    name: str,
    code: str,
    bank_code: str,
    bank_account_number: str,
    bank_account_name: str,
    sepay_sub_account: str = "",
    sepay_memo_prefix: str = "",
    meta_page_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "code": code,
        "bank_code": bank_code,
        "bank_account_number": bank_account_number,
        "bank_account_name": bank_account_name,
        "sepay_sub_account": sepay_sub_account,
        "sepay_memo_prefix": sepay_memo_prefix,
    }
    if meta_page_id is not None:
        payload["meta_page_id"] = meta_page_id
    resp = await client.post("/api/v1/companies", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _provision_active_manager(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    *,
    company_id: str,
    full_name: str,
    username: str,
    email: str,
    new_password: str = "ManagerPass!2026",
    tenant_id: str = "default-tenant",
) -> tuple[dict[str, Any], dict[str, str]]:
    create_resp = await client.post(
        f"/api/v1/companies/{company_id}/managers",
        json={"full_name": full_name, "username": username, "email": email},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    mgr_data = create_resp.json()
    assert mgr_data["must_change_password"] is True

    login_resp = await _signin(
        client,
        identifier=mgr_data["username"],
        password=mgr_data["temporary_password"],
        tenant_id=tenant_id,
    )
    assert login_resp.status_code == 200, login_resp.text
    temp_token = login_resp.json()["access_token"]

    cp_resp = await client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": mgr_data["temporary_password"],
            "new_password": new_password,
        },
        headers={"Authorization": f"Bearer {temp_token}"},
    )
    assert cp_resp.status_code == 200, cp_resp.text
    cp_tokens = cp_resp.json()
    assert cp_tokens["must_change_password"] is False
    return mgr_data, {"Authorization": f"Bearer {cp_tokens['access_token']}"}


# ------------------------------------------------------------------------------
# 1. Company Creation, Bank Setup, SePay Bank Hub & Manager Provisioning
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_company_bank_catalog_and_sepay_v2_sync_mock(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Verify curated NAPAS/SePay bank catalog and SePay API v2 linked bank account parsing."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unconfigured SePay API v2 still returns curated bank catalog cleanly
        monkeypatch.setattr(sepay_client, "api_key", "")
        monkeypatch.setattr(sepay_bankhub_client, "client_id", "")
        monkeypatch.setattr(sepay_bankhub_client, "client_secret", "")
        resp = await client.get("/api/v1/companies/banks", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        catalog = resp.json()
        assert catalog["sepay_api_configured"] is False
        assert catalog["bankhub_configured"] is False
        assert catalog["sepay_linked_accounts"] == []
        bank_codes = {b["code"] for b in catalog["banks"]}
        assert {"MB", "VCB", "TCB", "ACB", "BIDV", "CTG"}.issubset(bank_codes)

        # 2. Mock SePay API v2 /bank-accounts response
        class _FakeSepayResp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return {
                    "status": 200,
                    "bankaccounts": [
                        {
                            "id": "ba_901",
                            "bank_code": "MB",
                            "bank_short_name": "MBBank",
                            "account_number": "8800112233",
                            "account_holder_name": "CONG TY ALPHA",
                            "accumulated": 12500000,
                            "active": 1,
                        },
                        {
                            "id": "ba_902",
                            "bank_brand_name": "Vietcombank",
                            "bank_bin": "970436",
                            "accountNumber": "9900445566",
                            "account_name": "CONG TY BETA",
                            "active": True,
                        },
                    ],
                }

        class _FakeHttpClient:
            is_closed = False

            async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeSepayResp:
                assert url.endswith("/bank-accounts")
                assert headers and headers["Authorization"] == "Bearer sepay-v2-live-key"
                return _FakeSepayResp()

        monkeypatch.setattr(sepay_client, "api_key", "sepay-v2-live-key")
        monkeypatch.setattr(sepay_client, "_client", _FakeHttpClient())
        monkeypatch.setattr(sepay_client, "_client_cls", type(_FakeHttpClient()))

        async def _fake_get_client() -> _FakeHttpClient:
            return _FakeHttpClient()

        monkeypatch.setattr(sepay_client, "_get_client", _fake_get_client)

        resp2 = await client.get("/api/v1/companies/banks", headers=auth_headers)
        assert resp2.status_code == 200, resp2.text
        catalog2 = resp2.json()
        assert catalog2["sepay_api_configured"] is True
        assert len(catalog2["sepay_linked_accounts"]) == 2
        assert catalog2["sepay_linked_accounts"][0]["account_number"] == "8800112233"
        assert catalog2["sepay_linked_accounts"][0]["bank_short_name"] == "MBBank"
        assert catalog2["sepay_linked_accounts"][1]["account_number"] == "9900445566"


@pytest.mark.asyncio
async def test_admin_creates_companies_bank_setup_soft_delete_and_manager_rbac(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Admin creates multiple companies with SePay routing; manager/staff are blocked (403) from admin actions."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        comp_a = await _create_company(
            client,
            auth_headers,
            name="Saigon Roastery Co",
            code="SGN",
            bank_code="MB",
            bank_account_number="1111222233",
            bank_account_name="SAIGON ROASTERY",
            sepay_sub_account="VA_SGN",
            sepay_memo_prefix="SGN",
            meta_page_id="page_sgn_001",
        )
        comp_b = await _create_company(
            client,
            auth_headers,
            name="Hanoi Specialty Co",
            code="HAN",
            bank_code="VCB",
            bank_account_number="4444555566",
            bank_account_name="HANOI SPECIALTY",
            sepay_sub_account="VA_HAN",
            sepay_memo_prefix="HAN",
            meta_page_id="page_han_002",
        )
        assert comp_a["bank_name"] == "MBBank"
        assert comp_b["bank_name"] == "Vietcombank"

        # Duplicate code within tenant fails with 409
        dup_resp = await client.post(
            "/api/v1/companies",
            json={
                "name": "Duplicate Saigon",
                "code": "SGN",
                "bank_code": "MB",
                "bank_account_number": "7777888899",
                "bank_account_name": "DUP",
            },
            headers=auth_headers,
        )
        assert dup_resp.status_code == 409

        # Channel routing registered for both sepay_account and meta_page
        ctx_a_sepay = await erp_repository.resolve_channel_context(
            "sepay_account", "1111222233", "VA_SGN"
        )
        ctx_b_meta = await erp_repository.resolve_channel_context("meta_page", "page_han_002")
        assert ctx_a_sepay == ("default-tenant", UUID(comp_a["id"]))
        assert ctx_b_meta == ("default-tenant", UUID(comp_b["id"]))

        # Provision Manager A (auto-generated credentials) & activate via password change
        mgr_a, mgr_a_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp_a["id"],
            full_name="Le Thi Hoa",
            username="mgr_sgn_hoa",
            email="hoa@sgn.local",
        )
        assert mgr_a["company_id"] == comp_a["id"]
        assert mgr_a["role"] == "manager"

        # Admin lists company managers without leaking plaintext password
        mgr_list = await client.get(
            f"/api/v1/companies/{comp_a['id']}/managers",
            headers=auth_headers,
        )
        assert mgr_list.status_code == 200
        assert len(mgr_list.json()) == 1
        assert "temporary_password" not in mgr_list.json()[0]
        assert "password_hash" not in mgr_list.json()[0]

        # Manager listing companies sees ONLY their own company
        mgr_companies = await client.get("/api/v1/companies", headers=mgr_a_headers)
        assert mgr_companies.status_code == 200
        assert [c["id"] for c in mgr_companies.json()] == [comp_a["id"]]

        # Manager cannot view, update bank, or list managers of Company B (403)
        assert (
            await client.get(f"/api/v1/companies/{comp_b['id']}", headers=mgr_a_headers)
        ).status_code == 403
        assert (
            await client.patch(
                f"/api/v1/companies/{comp_b['id']}/bank",
                json={
                    "bank_code": "TCB",
                    "bank_account_number": "99990000",
                    "bank_account_name": "HACK",
                },
                headers=mgr_a_headers,
            )
        ).status_code == 403
        assert (
            await client.get(f"/api/v1/companies/{comp_b['id']}/managers", headers=mgr_a_headers)
        ).status_code == 403

        # SEC-02: Manager cannot update bank or trigger Bank Hub link on own company either (403, Admin only)
        assert (
            await client.patch(
                f"/api/v1/companies/{comp_a['id']}/bank",
                json={
                    "bank_code": "TCB",
                    "bank_account_number": "1900112233",
                    "bank_account_name": "SAIGON ROASTERY NEW",
                },
                headers=mgr_a_headers,
            )
        ).status_code == 403
        assert (
            await client.post(
                f"/api/v1/companies/{comp_a['id']}/sepay-bankhub-link",
                headers=mgr_a_headers,
            )
        ).status_code == 403

        # Admin can update Company A's bank details and re-sync SePay routing
        bank_upd = await client.patch(
            f"/api/v1/companies/{comp_a['id']}/bank",
            json={
                "bank_code": "TCB",
                "bank_account_number": "1900112233",
                "bank_account_name": "SAIGON ROASTERY NEW",
                "sepay_sub_account": "VA_SGN_V2",
                "sepay_memo_prefix": "SGN2",
            },
            headers=auth_headers,
        )
        assert bank_upd.status_code == 200, bank_upd.text
        assert bank_upd.json()["bank_code"] == "TCB"
        assert bank_upd.json()["bank_name"] == "Techcombank"
        assert await erp_repository.resolve_channel_context(
            "sepay_account", "1900112233", "VA_SGN_V2"
        ) == ("default-tenant", UUID(comp_a["id"]))

        # Bank Hub link unconfigured returns configured=False for Admin
        monkeypatch.setattr(sepay_bankhub_client, "client_id", "")
        monkeypatch.setattr(sepay_bankhub_client, "client_secret", "")
        bh_unconf = await client.post(
            f"/api/v1/companies/{comp_a['id']}/sepay-bankhub-link",
            headers=auth_headers,
        )
        assert bh_unconf.status_code == 200
        assert bh_unconf.json()["configured"] is False

        # Manager and Staff cannot create/delete/restore companies or create managers (403)
        assert (
            await client.post(
                "/api/v1/companies",
                json={
                    "name": "Unauthorized Co",
                    "code": "UNAUTH",
                    "bank_code": "MB",
                    "bank_account_number": "12345678",
                    "bank_account_name": "UNAUTH",
                },
                headers=mgr_a_headers,
            )
        ).status_code == 403
        assert (
            await client.delete(f"/api/v1/companies/{comp_a['id']}", headers=mgr_a_headers)
        ).status_code == 403
        assert (
            await client.post(
                f"/api/v1/companies/{comp_a['id']}/managers",
                json={"full_name": "Rogue Mgr"},
                headers=mgr_a_headers,
            )
        ).status_code == 403

        # Admin soft-deletes and restores Company B cleanly
        del_resp = await client.delete(f"/api/v1/companies/{comp_b['id']}", headers=auth_headers)
        assert del_resp.status_code == 200
        assert del_resp.json()["is_active"] is False

        active_only = await client.get("/api/v1/companies", headers=auth_headers)
        assert [c["id"] for c in active_only.json()] == [comp_a["id"]]

        res_resp = await client.post(
            f"/api/v1/companies/{comp_b['id']}/restore", headers=auth_headers
        )
        assert res_resp.status_code == 200
        assert res_resp.json()["is_active"] is True


# ------------------------------------------------------------------------------
# 2. Manager-Only Staff Provisioning & Forced First-Login Password Change
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_only_staff_creation_and_forced_password_change_flow(
    auth_headers: dict[str, str],
) -> None:
    """Only Manager creates Staff (with auto credentials); forced first-login password change gates workspace."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        comp = await _create_company(
            client,
            auth_headers,
            name="Da Nang Roasters",
            code="DAD",
            bank_code="ACB",
            bank_account_number="5555666677",
            bank_account_name="DANANG ROASTERS",
        )

        # Admin gets 403 calling POST /api/v1/erp/staff (Manager-only)
        admin_staff_attempt = await client.post(
            "/api/v1/erp/staff",
            json={"full_name": "Admin Forbidden Staff", "telegram_user_id": "111222"},
            headers=auth_headers,
        )
        assert admin_staff_attempt.status_code == 403

        # Provision manager and test manager's own must_change_password gate first
        mgr_create = await client.post(
            f"/api/v1/companies/{comp['id']}/managers",
            json={"full_name": "Manager DAD"},
            headers=auth_headers,
        )
        assert mgr_create.status_code == 201
        mgr_info = mgr_create.json()

        mgr_signin = await _signin(
            client,
            identifier=mgr_info["username"],
            password=mgr_info["temporary_password"],
        )
        assert mgr_signin.status_code == 200
        mgr_temp_headers = {"Authorization": f"Bearer {mgr_signin.json()['access_token']}"}
        assert mgr_signin.json()["must_change_password"] is True

        # Manager with must_change_password=True is blocked from ERP routes
        assert (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "Staff Before Password Change"},
                headers=mgr_temp_headers,
            )
        ).status_code == 403

        # Manager changes password -> unlocks workspace
        mgr_cp = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": mgr_info["temporary_password"],
                "new_password": "MgrStrongPassword!99",
            },
            headers=mgr_temp_headers,
        )
        assert mgr_cp.status_code == 200
        mgr_headers = {"Authorization": f"Bearer {mgr_cp.json()['access_token']}"}

        # Manager creates staff member with auto-generated credentials
        staff_create = await client.post(
            "/api/v1/erp/staff",
            json={
                "full_name": "Pham Van Khoa",
                "role": "packer",
                "telegram_user_id": "9988776655",
            },
            headers=mgr_headers,
        )
        assert staff_create.status_code == 201, staff_create.text
        staff = staff_create.json()
        assert staff["company_id"] == comp["id"]
        assert staff["username"].startswith("staff_")
        assert staff["temporary_password"]
        assert staff["must_change_password"] is True
        assert staff["employee_id"]
        assert staff["user_id"]

        # GET /api/v1/erp/staff must NEVER expose plaintext passwords or hashes
        staff_list = await client.get("/api/v1/erp/staff", headers=mgr_headers)
        assert staff_list.status_code == 200
        assert len(staff_list.json()) == 1
        row = staff_list.json()[0]
        assert row["username"] == staff["username"]
        assert row["must_change_password"] is True
        assert "temporary_password" not in row
        assert "password_hash" not in row
        assert "password" not in row

        # Staff signs in using username (AND verify email works too)
        staff_login_username = await _signin(
            client,
            identifier=staff["username"],
            password=staff["temporary_password"],
        )
        assert staff_login_username.status_code == 200, staff_login_username.text
        s_tokens = staff_login_username.json()
        assert s_tokens["must_change_password"] is True
        assert s_tokens["company_id"] == comp["id"]
        assert s_tokens["employee_id"] == staff["employee_id"]
        assert s_tokens["role"] == "staff"

        staff_login_email = await _signin(
            client,
            identifier=staff["email"],
            password=staff["temporary_password"],
        )
        assert staff_login_email.status_code == 200
        assert staff_login_email.json()["must_change_password"] is True

        temp_staff_headers = {"Authorization": f"Bearer {s_tokens['access_token']}"}

        # While must_change_password=True, workspace endpoints fail closed with 403
        for blocked_path in ("/api/v1/erp/my-work", "/api/v1/erp/orders", "/api/v1/companies"):
            blocked = await client.get(blocked_path, headers=temp_staff_headers)
            assert blocked.status_code == 403, f"Expected 403 for {blocked_path}, got {blocked.status_code}"

        # Wrong current password on change-password returns 401
        bad_cp = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "WrongTempPassword",
                "new_password": "StaffNewPassword!2026",
            },
            headers=temp_staff_headers,
        )
        assert bad_cp.status_code == 401

        # Valid change-password clears flag, rotates tokens, and unlocks /erp/my-work
        good_cp = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": staff["temporary_password"],
                "new_password": "StaffNewPassword!2026",
            },
            headers=temp_staff_headers,
        )
        assert good_cp.status_code == 200, good_cp.text
        rotated = good_cp.json()
        assert rotated["must_change_password"] is False
        assert rotated["access_token"] != s_tokens["access_token"]
        assert rotated["refresh_token"] != s_tokens["refresh_token"]
        active_staff_headers = {"Authorization": f"Bearer {rotated['access_token']}"}

        # /api/v1/auth/me reflects must_change_password=False
        me_resp = await client.get("/api/v1/auth/me", headers=active_staff_headers)
        assert me_resp.status_code == 200
        assert me_resp.json()["must_change_password"] is False
        assert me_resp.json()["company_id"] == comp["id"]
        assert me_resp.json()["employee_id"] == staff["employee_id"]

        # /api/v1/erp/my-work is now unlocked (returns 200)
        work_resp = await client.get("/api/v1/erp/my-work", headers=active_staff_headers)
        assert work_resp.status_code == 200
        assert work_resp.json() == []

        # Old temporary password is rejected; new password succeeds
        old_pw_login = await _signin(
            client,
            identifier=staff["username"],
            password=staff["temporary_password"],
        )
        assert old_pw_login.status_code == 401

        new_pw_login = await _signin(
            client,
            identifier=staff["username"],
            password="StaffNewPassword!2026",
        )
        assert new_pw_login.status_code == 200
        assert new_pw_login.json()["must_change_password"] is False

        # Staff cannot create other staff or companies (403)
        assert (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "Rogue Staff"},
                headers=active_staff_headers,
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/v1/companies",
                json={
                    "name": "Staff Co",
                    "code": "STF",
                    "bank_code": "MB",
                    "bank_account_number": "11223344",
                    "bank_account_name": "STF",
                },
                headers=active_staff_headers,
            )
        ).status_code == 403


# ------------------------------------------------------------------------------
# 3. Cross-Company Isolation (U-10) & Role Boundaries
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_company_isolation_and_admin_filtering(
    auth_headers: dict[str, str],
) -> None:
    """Company A Manager cannot access Company B data; Admin sees all and filters by company_id."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        comp_a = await _create_company(
            client,
            auth_headers,
            name="Alpha Coffee",
            code="ALPHA",
            bank_code="MB",
            bank_account_number="20001111",
            bank_account_name="ALPHA COFFEE",
            sepay_memo_prefix="ALP",
        )
        comp_b = await _create_company(
            client,
            auth_headers,
            name="Beta Tea",
            code="BETA",
            bank_code="VCB",
            bank_account_number="30002222",
            bank_account_name="BETA TEA",
            sepay_memo_prefix="BET",
        )
        comp_a_uuid = UUID(comp_a["id"])
        comp_b_uuid = UUID(comp_b["id"])

        _, mgr_a_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp_a["id"],
            full_name="Alpha Manager",
            username="mgr_alpha",
            email="alpha@test.local",
        )
        _, mgr_b_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp_b["id"],
            full_name="Beta Manager",
            username="mgr_beta",
            email="beta@test.local",
        )

        # Create inventory items in Company A and Company B
        inv_a = (
            await client.post(
                "/api/v1/erp/inventory",
                json={
                    "sku": "ALP-BEAN-1",
                    "name": "Alpha Espresso Blend",
                    "description": "Dark roast alpha espresso",
                    "price_vnd": 350000,
                    "stock_quantity": 20,
                },
                headers=mgr_a_headers,
            )
        )
        assert inv_a.status_code == 201
        inv_a_data = inv_a.json()
        assert inv_a_data["company_id"] == comp_a["id"]

        inv_b = (
            await client.post(
                "/api/v1/erp/inventory",
                json={
                    "sku": "BET-TEA-1",
                    "name": "Beta Matcha Uji",
                    "description": "Ceremonial matcha from Beta",
                    "price_vnd": 450000,
                    "stock_quantity": 15,
                },
                headers=mgr_b_headers,
            )
        )
        assert inv_b.status_code == 201
        inv_b_data = inv_b.json()
        assert inv_b_data["company_id"] == comp_b["id"]

        # Create staff, customers, and orders in each company
        staff_a = (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "Alpha Staff", "telegram_user_id": "101010"},
                headers=mgr_a_headers,
            )
        ).json()
        staff_b = (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "Beta Staff", "telegram_user_id": "202020"},
                headers=mgr_b_headers,
            )
        ).json()

        cust_a = erp_repository.get_or_create_customer_by_psid(
            meta_psid="psid_alpha_1",
            full_name="Alpha Buyer",
            tenant_id="default-tenant",
            company_id=comp_a_uuid,
        )
        cust_b = erp_repository.get_or_create_customer_by_psid(
            meta_psid="psid_beta_1",
            full_name="Beta Buyer",
            tenant_id="default-tenant",
            company_id=comp_b_uuid,
        )

        order_a = (
            await client.post(
                "/api/v1/erp/orders",
                json={
                    "customer_id": str(cust_a.id),
                    "assigned_employee_id": staff_a["employee_id"],
                    "items": [{"sku": "ALP-BEAN-1", "quantity": 2}],
                },
                headers=mgr_a_headers,
            )
        )
        assert order_a.status_code == 201
        order_a_data = order_a.json()

        order_b = (
            await client.post(
                "/api/v1/erp/orders",
                json={
                    "customer_id": str(cust_b.id),
                    "assigned_employee_id": staff_b["employee_id"],
                    "items": [{"sku": "BET-TEA-1", "quantity": 1}],
                },
                headers=mgr_b_headers,
            )
        )
        assert order_b.status_code == 201
        order_b_data = order_b.json()

        # Default lists for Manager A show ZERO Company B records
        a_inv_list = (await client.get("/api/v1/erp/inventory", headers=mgr_a_headers)).json()
        assert {i["sku"] for i in a_inv_list} == {"ALP-BEAN-1"}

        a_orders = (await client.get("/api/v1/erp/orders", headers=mgr_a_headers)).json()
        assert [o["id"] for o in a_orders] == [order_a_data["id"]]

        a_customers = (await client.get("/api/v1/erp/customers", headers=mgr_a_headers)).json()
        assert [c["id"] for c in a_customers] == [str(cust_a.id)]

        a_staff = (await client.get("/api/v1/erp/staff", headers=mgr_a_headers)).json()
        assert [s["employee_id"] for s in a_staff] == [staff_a["employee_id"]]

        # Explicit cross-company query, header, payload, and resource access return 403 or 404
        assert (
            await client.get(
                f"/api/v1/erp/inventory?company_id={comp_b['id']}", headers=mgr_a_headers
            )
        ).status_code == 403
        assert (
            await client.get(
                "/api/v1/erp/orders",
                headers={**mgr_a_headers, "X-Company-Id": comp_b["id"]},
            )
        ).status_code == 403
        assert (
            await client.get(
                f"/api/v1/erp/dashboard/manager?company_id={comp_b['id']}",
                headers=mgr_a_headers,
            )
        ).status_code == 403
        assert (
            await client.get(
                f"/api/v1/erp/chats?company_id={comp_b['id']}", headers=mgr_a_headers
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/v1/erp/orders",
                json={
                    "company_id": comp_b["id"],
                    "customer_id": str(cust_b.id),
                    "items": [{"sku": "BET-TEA-1", "quantity": 1}],
                },
                headers=mgr_a_headers,
            )
        ).status_code == 403
        # Using Company A manager without specifying company_id against Company B customer/SKU fails
        assert (
            await client.post(
                "/api/v1/erp/orders",
                json={
                    "customer_id": str(cust_b.id),
                    "items": [{"sku": "ALP-BEAN-1", "quantity": 1}],
                },
                headers=mgr_a_headers,
            )
        ).status_code == 404
        assert (
            await client.patch(
                f"/api/v1/erp/orders/{order_b_data['id']}/status",
                json={"status": "CANCELLED"},
                headers=mgr_a_headers,
            )
        ).status_code == 403
        assert (
            await client.post(
                f"/api/v1/erp/orders/{order_b_data['id']}/notify-telegram",
                headers=mgr_a_headers,
            )
        ).status_code == 403
        assert (
            await client.delete(
                f"/api/v1/erp/inventory/{inv_b_data['id']}",
                headers=mgr_a_headers,
            )
        ).status_code == 404

        # Admin sees cross-company overview AND can filter by specific company_id
        all_orders = (await client.get("/api/v1/erp/orders", headers=auth_headers)).json()
        all_order_ids = {o["id"] for o in all_orders}
        assert {order_a_data["id"], order_b_data["id"]}.issubset(all_order_ids)

        only_b_orders = (
            await client.get(
                f"/api/v1/erp/orders?company_id={comp_b['id']}", headers=auth_headers
            )
        ).json()
        assert [o["id"] for o in only_b_orders] == [order_b_data["id"]]

        only_a_inv = (
            await client.get(
                "/api/v1/erp/inventory",
                headers={**auth_headers, "X-Company-Id": comp_a["id"]},
            )
        ).json()
        assert [i["sku"] for i in only_a_inv] == ["ALP-BEAN-1"]


# ------------------------------------------------------------------------------
# 4. Staff Work-Only Scope & Status Updates
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_work_only_scope_and_status_transitions(
    auth_headers: dict[str, str],
) -> None:
    """Staff only sees/updates their own assigned orders, supports in_progress alias, and is blocked from manager routes."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        comp = await _create_company(
            client,
            auth_headers,
            name="Fulfillment Co",
            code="FUL",
            bank_code="MB",
            bank_account_number="40001111",
            bank_account_name="FULFILLMENT CO",
        )
        _, mgr_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp["id"],
            full_name="Fulfillment Mgr",
            username="mgr_ful",
            email="mgr@ful.local",
        )

        # Create 2 staff members in the same company
        async def _create_and_activate_staff(username: str, tg_id: str) -> tuple[str, dict[str, str]]:
            s_resp = await client.post(
                "/api/v1/erp/staff",
                json={
                    "full_name": f"Staff {username}",
                    "username": username,
                    "telegram_user_id": tg_id,
                },
                headers=mgr_headers,
            )
            assert s_resp.status_code == 201
            s_data = s_resp.json()
            s_login = await _signin(
                client, identifier=s_data["username"], password=s_data["temporary_password"]
            )
            s_cp = await client.post(
                "/api/v1/auth/change-password",
                json={
                    "current_password": s_data["temporary_password"],
                    "new_password": "StaffActivePass!123",
                },
                headers={"Authorization": f"Bearer {s_login.json()['access_token']}"},
            )
            assert s_cp.status_code == 200
            return s_data["employee_id"], {"Authorization": f"Bearer {s_cp.json()['access_token']}"}

        emp1_id, staff1_headers = await _create_and_activate_staff("staff_one", "111000")
        emp2_id, staff2_headers = await _create_and_activate_staff("staff_two", "222000")

        # Seed company inventory & customer
        await client.post(
            "/api/v1/erp/inventory",
            json={
                "sku": "FUL-SKU-1",
                "name": "Fulfillment Product",
                "description": "Product for fulfillment workflow",
                "price_vnd": 150000,
                "stock_quantity": 30,
            },
            headers=mgr_headers,
        )
        cust = erp_repository.get_or_create_customer_by_psid(
            meta_psid="psid_ful_cust",
            full_name="Fulfillment Customer",
            tenant_id="default-tenant",
            company_id=UUID(comp["id"]),
        )

        order_1 = (
            await client.post(
                "/api/v1/erp/orders",
                json={
                    "customer_id": str(cust.id),
                    "assigned_employee_id": emp1_id,
                    "items": [{"sku": "FUL-SKU-1", "quantity": 2}],
                },
                headers=mgr_headers,
            )
        ).json()
        order_2 = (
            await client.post(
                "/api/v1/erp/orders",
                json={
                    "customer_id": str(cust.id),
                    "assigned_employee_id": emp2_id,
                    "items": [{"sku": "FUL-SKU-1", "quantity": 1}],
                },
                headers=mgr_headers,
            )
        ).json()

        # Staff 1 sees ONLY order_1 on both /my-work and /orders
        s1_my_work = (await client.get("/api/v1/erp/my-work", headers=staff1_headers)).json()
        assert [o["id"] for o in s1_my_work] == [order_1["id"]]

        s1_orders = (await client.get("/api/v1/erp/orders", headers=staff1_headers)).json()
        assert [o["id"] for o in s1_orders] == [order_1["id"]]

        # Staff 2 sees ONLY order_2
        s2_my_work = (await client.get("/api/v1/erp/my-work", headers=staff2_headers)).json()
        assert [o["id"] for o in s2_my_work] == [order_2["id"]]

        # Staff cannot mark PENDING_PAYMENT -> PAID manually (403)
        manual_paid = await client.patch(
            f"/api/v1/erp/orders/{order_1['id']}/status",
            json={"status": "PAID"},
            headers=staff1_headers,
        )
        assert manual_paid.status_code == 403

        # Admin reconciles order_1 -> PAID
        admin_paid = await client.patch(
            f"/api/v1/erp/orders/{order_1['id']}/status",
            json={"status": "PAID"},
            headers=auth_headers,
        )
        assert admin_paid.status_code == 200
        assert admin_paid.json()["status"] == "PAID"

        # Staff 1 transitions PAID -> FULFILLING via "in_progress" alias, then FULFILLING -> COMPLETED
        to_fulfilling = await client.patch(
            f"/api/v1/erp/orders/{order_1['id']}/status",
            json={"status": "in_progress"},
            headers=staff1_headers,
        )
        assert to_fulfilling.status_code == 200, to_fulfilling.text
        assert to_fulfilling.json()["status"] == "FULFILLING"

        to_completed = await client.patch(
            f"/api/v1/erp/orders/{order_1['id']}/status",
            json={"status": "COMPLETED"},
            headers=staff1_headers,
        )
        assert to_completed.status_code == 200
        assert to_completed.json()["status"] == "COMPLETED"

        # Invalid transition COMPLETED -> FULFILLING returns 409
        invalid_trans = await client.patch(
            f"/api/v1/erp/orders/{order_1['id']}/status",
            json={"status": "FULFILLING"},
            headers=staff1_headers,
        )
        assert invalid_trans.status_code == 409

        # Staff 1 cannot update Staff 2's order (403)
        cross_staff = await client.patch(
            f"/api/v1/erp/orders/{order_2['id']}/status",
            json={"status": "CANCELLED"},
            headers=staff1_headers,
        )
        assert cross_staff.status_code == 403

        # Staff is forbidden (403) on Manager/Admin workspace endpoints
        for method, path in (
            ("GET", "/api/v1/erp/dashboard/manager"),
            ("GET", "/api/v1/erp/customers"),
            ("GET", "/api/v1/erp/employees"),
            ("GET", "/api/v1/erp/chats"),
            ("GET", "/api/v1/erp/staff"),
            ("POST", "/api/v1/erp/staff"),
            ("GET", "/api/v1/erp/metrics"),
            ("GET", "/api/v1/erp/webhooks/logs"),
            ("POST", "/api/v1/erp/orders"),
            ("POST", "/api/v1/erp/inventory"),
        ):
            r = await client.request(method, path, headers=staff1_headers)
            assert r.status_code == 403, f"Expected 403 for staff on {method} {path}, got {r.status_code}"


# ------------------------------------------------------------------------------
# 5. Per-Company SePay VietQR & Sub-Account Webhook Routing
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_company_vietqr_and_sepay_subaccount_webhook_routing(
    auth_headers: dict[str, str],
) -> None:
    """Orders generate Company-specific VietQR URLs and SePay IPN routes by (accountNumber, subAccount)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Two companies sharing the same base bank account but distinct sub-accounts & memo prefixes
        comp_a = await _create_company(
            client,
            auth_headers,
            name="Branch North",
            code="NORTH",
            bank_code="MB",
            bank_account_number="77770000",
            bank_account_name="BRANCH NORTH",
            sepay_sub_account="VA_NORTH",
            sepay_memo_prefix="NRT",
        )
        comp_b = await _create_company(
            client,
            auth_headers,
            name="Branch South",
            code="SOUTH",
            bank_code="VCB",
            bank_account_number="77770000",
            bank_account_name="BRANCH SOUTH",
            sepay_sub_account="VA_SOUTH",
            sepay_memo_prefix="STH",
        )
        _, mgr_a_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp_a["id"],
            full_name="North Mgr",
            username="mgr_north",
            email="north@test.local",
        )
        _, mgr_b_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp_b["id"],
            full_name="South Mgr",
            username="mgr_south",
            email="south@test.local",
        )

        staff_a = (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "North Staff", "telegram_user_id": "555111"},
                headers=mgr_a_headers,
            )
        ).json()
        staff_b = (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "South Staff", "telegram_user_id": "555222"},
                headers=mgr_b_headers,
            )
        ).json()

        await client.post(
            "/api/v1/erp/inventory",
            json={
                "sku": "NRT-1",
                "name": "North Roast",
                "description": "North specialty",
                "price_vnd": 250000,
                "stock_quantity": 10,
            },
            headers=mgr_a_headers,
        )
        await client.post(
            "/api/v1/erp/inventory",
            json={
                "sku": "STH-1",
                "name": "South Roast",
                "description": "South specialty",
                "price_vnd": 300000,
                "stock_quantity": 10,
            },
            headers=mgr_b_headers,
        )

        cust_a = erp_repository.get_or_create_customer_by_psid(
            meta_psid="psid_north",
            full_name="North Buyer",
            tenant_id="default-tenant",
            company_id=UUID(comp_a["id"]),
        )
        cust_b = erp_repository.get_or_create_customer_by_psid(
            meta_psid="psid_south",
            full_name="South Buyer",
            tenant_id="default-tenant",
            company_id=UUID(comp_b["id"]),
        )

        order_a = (
            await client.post(
                "/api/v1/erp/orders",
                json={
                    "customer_id": str(cust_a.id),
                    "assigned_employee_id": staff_a["employee_id"],
                    "items": [{"sku": "NRT-1", "quantity": 2}],
                },
                headers=mgr_a_headers,
            )
        ).json()
        order_b = (
            await client.post(
                "/api/v1/erp/orders",
                json={
                    "customer_id": str(cust_b.id),
                    "assigned_employee_id": staff_b["employee_id"],
                    "items": [{"sku": "STH-1", "quantity": 1}],
                },
                headers=mgr_b_headers,
            )
        ).json()

        assert order_a["company_id"] == comp_a["id"]
        assert order_a["company_name"] == "Branch North"
        assert order_b["company_id"] == comp_b["id"]
        assert order_b["company_name"] == "Branch South"

        # Verify VietQR query parameters per company
        qs_a = parse_qs(urlparse(order_a["sepay_qr_url"]).query)
        assert qs_a["acc"] == ["77770000"]
        assert qs_a["bank"] == ["MB"]
        assert qs_a["amount"] == ["500000"]
        assert qs_a["des"] == [f"NRT {order_a['order_code']}"]

        qs_b = parse_qs(urlparse(order_b["sepay_qr_url"]).query)
        assert qs_b["acc"] == ["77770000"]
        assert qs_b["bank"] == ["VCB"]
        assert qs_b["amount"] == ["300000"]
        assert qs_b["des"] == [f"STH {order_b['order_code']}"]

        # Cross-company IPN mismatch: North sub-account sent with South order_code must NOT settle South order
        wrong_sub_ipn = await client.post(
            "/api/v1/webhooks/sepay",
            json={
                "id": 700001,
                "gateway": "MBBank",
                "accountNumber": "77770000",
                "subAccount": "VA_NORTH",
                "content": f"NRT {order_b['order_code']}",
                "transferType": "in",
                "transferAmount": 300000,
            },
            headers={"Authorization": "Apikey sepay-sandbox-secret"},
        )
        assert wrong_sub_ipn.status_code == 200
        assert wrong_sub_ipn.json()["ignored"] is True
        assert wrong_sub_ipn.json()["reason"] == "order_not_found"

        # Valid South sub-account IPN settles South order and alerts South staff
        valid_south_ipn = await client.post(
            "/api/v1/webhooks/sepay",
            json={
                "id": 700002,
                "gateway": "Vietcombank",
                "accountNumber": "77770000",
                "subAccount": "VA_SOUTH",
                "content": f"STH{order_b['order_code']}",
                "transferType": "in",
                "transferAmount": 300000,
            },
            headers={"Authorization": "Apikey sepay-sandbox-secret"},
        )
        assert valid_south_ipn.status_code == 200, valid_south_ipn.text
        south_res = valid_south_ipn.json()
        assert south_res["order_code"] == order_b["order_code"]
        assert south_res["order_status"] == "PAID"
        assert south_res["telegram_dispatch"]["telegram_user_id"] == "555222"

        # Webhook logs scoped by company_id
        b_logs = (await client.get("/api/v1/erp/webhooks/logs", headers=mgr_b_headers)).json()
        assert len(b_logs) == 1
        assert b_logs[0]["sepay_transaction_id"] == "700002"
        assert b_logs[0]["company_id"] == comp_b["id"]


# ------------------------------------------------------------------------------
# 6. Manager Dashboard (Revenue + Daily Income) & Omnichannel Chats
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_dashboard_revenue_daily_income_and_omnichannel_chats(
    auth_headers: dict[str, str],
    meta_signature_headers: Callable[[bytes, str | None], dict[str, str]],
) -> None:
    """Verify Manager dashboard analytics and Meta Messenger/Instagram chat persistence + reply isolation."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        comp_a = await _create_company(
            client,
            auth_headers,
            name="Omnichannel Alpha",
            code="OMNIA",
            bank_code="ACB",
            bank_account_number="88881111",
            bank_account_name="OMNICHANNEL ALPHA",
            sepay_sub_account="VA_OMNIA",
            sepay_memo_prefix="OA",
            meta_page_id="page_omnia_01",
        )
        comp_b = await _create_company(
            client,
            auth_headers,
            name="Omnichannel Beta",
            code="OMNIB",
            bank_code="TPB",
            bank_account_number="88882222",
            bank_account_name="OMNICHANNEL BETA",
            sepay_sub_account="VA_OMNIB",
            sepay_memo_prefix="OB",
            meta_page_id="page_omnib_02",
        )
        # Register Instagram page_id for Company A as well
        await erp_repository.register_company_channel(
            tenant_id="default-tenant",
            company_id=UUID(comp_a["id"]),
            channel_type="meta_page",
            external_id="ig_omnia_01",
        )

        _, mgr_a_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp_a["id"],
            full_name="Omnia Manager",
            username="mgr_omnia",
            email="mgr@omnia.local",
        )
        _, mgr_b_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp_b["id"],
            full_name="Omnib Manager",
            username="mgr_omnib",
            email="mgr@omnib.local",
        )

        # Provision staff and inventory for Company A and Company B
        staff_a = (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "Omnia Staff", "telegram_user_id": "333111"},
                headers=mgr_a_headers,
            )
        ).json()
        await client.post(
            "/api/v1/erp/inventory",
            json={
                "sku": "OMNIA-BLEND",
                "name": "Omnia Special Coffee Blend",
                "description": "Specialty Arabica coffee blend for Messenger & Instagram buyers",
                "price_vnd": 400000,
                "stock_quantity": 25,
            },
            headers=mgr_a_headers,
        )
        await client.post(
            "/api/v1/erp/inventory",
            json={
                "sku": "OMNIB-TEA",
                "name": "Omnib Oolong Tea",
                "description": "Highland oolong tea",
                "price_vnd": 900000,
                "stock_quantity": 10,
            },
            headers=mgr_b_headers,
        )

        # 1. Send Messenger webhook to Company A (with purchase intent) and Instagram DM to Company A (inquiry)
        msg_payload = {
            "object": "page",
            "entry": [
                {
                    "id": "page_omnia_01",
                    "messaging": [
                        {
                            "sender": {"id": "psid_omnia_msg_1"},
                            "recipient": {"id": "page_omnia_01"},
                            "message": {
                                "mid": "mid_omnia_msg_001",
                                "text": "Cho mình mua 2 gói Omnia Special Coffee Blend",
                            },
                        }
                    ],
                }
            ],
        }
        msg_raw = json.dumps(msg_payload, separators=(",", ":")).encode("utf-8")
        msg_wh = await client.post(
            "/api/v1/webhooks/meta",
            content=msg_raw,
            headers=meta_signature_headers(msg_raw, None),
        )
        assert msg_wh.status_code == 200, msg_wh.text
        msg_ev = msg_wh.json()["events"][0]
        assert msg_ev["order_intent_detected"] is True
        paid_order_code = msg_ev["order_code"]
        assert paid_order_code is not None

        ig_payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "ig_omnia_01",
                    "messaging": [
                        {
                            "sender": {"id": "igsid_omnia_2"},
                            "recipient": {"id": "ig_omnia_01"},
                            "message": {
                                "mid": "mid_omnia_ig_002",
                                "text": "Shop còn cà phê rang xay không?",
                            },
                        }
                    ],
                }
            ],
        }
        ig_raw = json.dumps(ig_payload, separators=(",", ":")).encode("utf-8")
        ig_wh = await client.post(
            "/api/v1/webhooks/meta",
            content=ig_raw,
            headers=meta_signature_headers(ig_raw, None),
        )
        assert ig_wh.status_code == 200, ig_wh.text

        # Send Messenger webhook to Company B
        b_msg_payload = {
            "object": "page",
            "entry": [
                {
                    "id": "page_omnib_02",
                    "messaging": [
                        {
                            "sender": {"id": "psid_omnib_1"},
                            "recipient": {"id": "page_omnib_02"},
                            "message": {
                                "mid": "mid_omnib_001",
                                "text": "Cho mình mua 1 hộp Omnib Oolong Tea",
                            },
                        }
                    ],
                }
            ],
        }
        b_raw = json.dumps(b_msg_payload, separators=(",", ":")).encode("utf-8")
        b_wh = await client.post(
            "/api/v1/webhooks/meta",
            content=b_raw,
            headers=meta_signature_headers(b_raw, None),
        )
        assert b_wh.status_code == 200
        b_order_code = b_wh.json()["events"][0]["order_code"]
        assert b_order_code is not None

        # Settle Company A's Messenger order via SePay IPN (800,000 VND) and create one pending order
        sepay_a = await client.post(
            "/api/v1/webhooks/sepay",
            json={
                "id": 880011,
                "accountNumber": "88881111",
                "subAccount": "VA_OMNIA",
                "content": f"OA {paid_order_code}",
                "transferType": "in",
                "transferAmount": 800000,
            },
            headers={"Authorization": "Apikey sepay-sandbox-secret"},
        )
        assert sepay_a.status_code == 200
        assert sepay_a.json()["order_status"] == "PAID"

        # Settle Company B's order (900,000 VND) to confirm it does NOT pollute Company A's revenue
        sepay_b = await client.post(
            "/api/v1/webhooks/sepay",
            json={
                "id": 880022,
                "accountNumber": "88882222",
                "subAccount": "VA_OMNIB",
                "content": f"OB {b_order_code}",
                "transferType": "in",
                "transferAmount": 900000,
            },
            headers={"Authorization": "Apikey sepay-sandbox-secret"},
        )
        assert sepay_b.status_code == 200
        assert sepay_b.json()["order_status"] == "PAID"

        # Create one pending order in Company A assigned to staff_a
        cust_a_list = (await client.get("/api/v1/erp/customers", headers=mgr_a_headers)).json()
        assert len(cust_a_list) == 2
        pending_a = await client.post(
            "/api/v1/erp/orders",
            json={
                "customer_id": cust_a_list[0]["id"],
                "assigned_employee_id": staff_a["employee_id"],
                "items": [{"sku": "OMNIA-BLEND", "quantity": 1}],
            },
            headers=mgr_a_headers,
        )
        assert pending_a.status_code == 201

        # 2. Verify Manager Dashboard analytics for Company A
        dash_resp = await client.get(
            "/api/v1/erp/dashboard/manager?days=7",
            headers=mgr_a_headers,
        )
        assert dash_resp.status_code == 200, dash_resp.text
        dash = dash_resp.json()
        assert dash["company_id"] == comp_a["id"]
        assert dash["company_name"] == "Omnichannel Alpha"
        assert dash["total_revenue_vnd"] == 800000
        assert dash["paid_orders_count"] == 1
        assert dash["pending_orders_count"] == 1
        assert dash["total_orders_count"] == 2
        assert dash["active_customers_count"] == 2
        assert dash["active_staff_count"] == 1
        assert dash["open_chats_count"] == 2
        assert len(dash["daily_income"]) == 7
        assert dash["daily_income"][-1]["revenue_vnd"] == 800000
        assert dash["daily_income"][-1]["orders_count"] == 1
        assert len(dash["staff_workload"]) == 1
        assert dash["staff_workload"][0]["active_orders_count"] == 2

        # 3. Verify Omnichannel Chats listing, channel filtering, messages, and reply
        chats_a = (await client.get("/api/v1/erp/chats", headers=mgr_a_headers)).json()
        assert len(chats_a) == 2
        assert {c["channel"] for c in chats_a} == {"messenger", "instagram"}
        assert all(c["company_id"] == comp_a["id"] for c in chats_a)

        ig_only = (
            await client.get("/api/v1/erp/chats?channel=instagram", headers=mgr_a_headers)
        ).json()
        assert len(ig_only) == 1
        ig_conv_id = ig_only[0]["id"]
        assert ig_only[0]["unread_count"] == 1

        # Messages include inbound customer message + outbound ai_bot message
        ig_msgs = (
            await client.get(
                f"/api/v1/erp/chats/{ig_conv_id}/messages", headers=mgr_a_headers
            )
        ).json()
        assert len(ig_msgs) == 2
        assert ig_msgs[0]["direction"] == "inbound"
        assert ig_msgs[0]["sender_type"] == "customer"
        assert ig_msgs[0]["channel"] == "instagram"
        assert ig_msgs[1]["direction"] == "outbound"
        assert ig_msgs[1]["sender_type"] == "ai_bot"

        # Manager replies -> persists outbound manager message and resets unread_count to 0
        reply_resp = await client.post(
            f"/api/v1/erp/chats/{ig_conv_id}/reply",
            json={"content": "Chào bạn, shop còn sẵn Omnia Special Coffee Blend ạ!"},
            headers=mgr_a_headers,
        )
        assert reply_resp.status_code == 201, reply_resp.text
        reply_data = reply_resp.json()
        assert reply_data["direction"] == "outbound"
        assert reply_data["sender_type"] == "manager"
        assert reply_data["company_id"] == comp_a["id"]

        ig_msgs_after = (
            await client.get(
                f"/api/v1/erp/chats/{ig_conv_id}/messages", headers=mgr_a_headers
            )
        ).json()
        assert len(ig_msgs_after) == 3

        ig_only_after = (
            await client.get("/api/v1/erp/chats?channel=instagram", headers=mgr_a_headers)
        ).json()
        assert ig_only_after[0]["unread_count"] == 0

        # Company B Manager cannot read or reply to Company A's conversation (403)
        assert (
            await client.get(
                f"/api/v1/erp/chats/{ig_conv_id}/messages", headers=mgr_b_headers
            )
        ).status_code == 403
        assert (
            await client.post(
                f"/api/v1/erp/chats/{ig_conv_id}/reply",
                json={"content": "Cross-company intrusion"},
                headers=mgr_b_headers,
            )
        ).status_code == 403


# ------------------------------------------------------------------------------
# 7. Rework Iteration 1 Security & Atomicity Regression Tests (SEC-01..SEC-04)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sec01_change_password_revokes_prior_access_and_refresh_tokens(
    auth_headers: dict[str, str],
) -> None:
    """SEC-01: Old pre-change access and refresh tokens are immediately rejected (401) after password rotation."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        comp = await _create_company(
            client,
            auth_headers,
            name="Security Auth Co",
            code="SECAUTH",
            bank_code="MB",
            bank_account_number="61001122",
            bank_account_name="SECURITY AUTH CO",
        )
        _, mgr_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp["id"],
            full_name="Security Mgr",
            username="mgr_secauth",
            email="mgr@secauth.local",
        )
        staff = (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "Security Staff", "telegram_user_id": "777888"},
                headers=mgr_headers,
            )
        ).json()

        login_resp = await _signin(
            client,
            identifier=staff["username"],
            password=staff["temporary_password"],
        )
        assert login_resp.status_code == 200
        old_pair = login_resp.json()
        old_access = old_pair["access_token"]
        old_refresh = old_pair["refresh_token"]

        cp_resp = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": staff["temporary_password"],
                "new_password": "RotatedStrongPassword!2026",
            },
            headers={"Authorization": f"Bearer {old_access}"},
        )
        assert cp_resp.status_code == 200, cp_resp.text
        new_pair = cp_resp.json()
        new_access = new_pair["access_token"]
        new_refresh = new_pair["refresh_token"]

        # Old access token must be rejected with 401 on /auth/me, /erp/my-work, and /companies
        for path in ("/api/v1/auth/me", "/api/v1/erp/my-work", "/api/v1/companies"):
            revoked_resp = await client.get(
                path, headers={"Authorization": f"Bearer {old_access}"}
            )
            assert revoked_resp.status_code == 401, (
                f"Expected 401 for revoked old access token on {path}, got {revoked_resp.status_code}"
            )

        # Old refresh token must be rejected with 401 on /auth/refresh
        old_refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": old_refresh},
        )
        assert old_refresh_resp.status_code == 401, old_refresh_resp.text

        # New access and refresh tokens succeed (200)
        assert (
            await client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"}
            )
        ).status_code == 200
        assert (
            await client.get(
                "/api/v1/erp/my-work", headers={"Authorization": f"Bearer {new_access}"}
            )
        ).status_code == 200
        new_refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": new_refresh},
        )
        assert new_refresh_resp.status_code == 200, new_refresh_resp.text
        assert new_refresh_resp.json()["must_change_password"] is False


@pytest.mark.asyncio
async def test_sec02_company_bank_admin_only_and_atomic_duplicate_channel_rollback(
    auth_headers: dict[str, str],
) -> None:
    """SEC-02: Bank/BankHub endpoints are Admin-only (403 for manager/staff) and duplicate channels fail atomically (409)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        comp_a = await _create_company(
            client,
            auth_headers,
            name="Primary Bank Co",
            code="PBANK",
            bank_code="MB",
            bank_account_number="99112233",
            bank_account_name="PRIMARY BANK CO",
            sepay_sub_account="VA_PRIMARY",
            sepay_memo_prefix="PB",
        )
        comp_b = await _create_company(
            client,
            auth_headers,
            name="Secondary Bank Co",
            code="SBANK",
            bank_code="VCB",
            bank_account_number="99445566",
            bank_account_name="SECONDARY BANK CO",
            sepay_sub_account="VA_SECONDARY",
            sepay_memo_prefix="SB",
        )
        _, mgr_a_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp_a["id"],
            full_name="Primary Mgr",
            username="mgr_pbank",
            email="mgr@pbank.local",
        )
        staff_a = (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "Primary Staff", "telegram_user_id": "123123"},
                headers=mgr_a_headers,
            )
        ).json()
        s_login = await _signin(
            client,
            identifier=staff_a["username"],
            password=staff_a["temporary_password"],
        )
        s_cp = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": staff_a["temporary_password"],
                "new_password": "StaffBankGuard!2026",
            },
            headers={"Authorization": f"Bearer {s_login.json()['access_token']}"},
        )
        staff_a_headers = {"Authorization": f"Bearer {s_cp.json()['access_token']}"}

        # Manager and Staff get 403 on PATCH /companies/{id}/bank and POST /companies/{id}/sepay-bankhub-link
        for actor_headers in (mgr_a_headers, staff_a_headers):
            bank_patch = await client.patch(
                f"/api/v1/companies/{comp_a['id']}/bank",
                json={
                    "bank_code": "TCB",
                    "bank_account_number": "12345678",
                    "bank_account_name": "UNAUTHORIZED",
                },
                headers=actor_headers,
            )
            assert bank_patch.status_code == 403

            bh_link = await client.post(
                f"/api/v1/companies/{comp_a['id']}/sepay-bankhub-link",
                headers=actor_headers,
            )
            assert bh_link.status_code == 403

        # Duplicate SePay channel on create_company fails with 409, leaves NO orphaned company, and keeps Company A routing
        hijack_create = await client.post(
            "/api/v1/companies",
            json={
                "name": "Hijack Channel Co",
                "code": "HIJACK",
                "bank_code": "MB",
                "bank_account_number": "99112233",
                "bank_account_name": "HIJACK CO",
                "sepay_sub_account": "VA_PRIMARY",
            },
            headers=auth_headers,
        )
        assert hijack_create.status_code == 409, hijack_create.text

        companies_after = (
            await client.get("/api/v1/companies?include_inactive=true", headers=auth_headers)
        ).json()
        assert {c["code"] for c in companies_after} == {"PBANK", "SBANK"}
        assert await erp_repository.resolve_channel_context(
            "sepay_account", "99112233", "VA_PRIMARY"
        ) == ("default-tenant", UUID(comp_a["id"]))

        # Duplicate SePay channel on update_company_bank fails with 409 and preserves both companies cleanly
        hijack_update = await client.patch(
            f"/api/v1/companies/{comp_b['id']}/bank",
            json={
                "bank_code": "MB",
                "bank_account_number": "99112233",
                "bank_account_name": "HIJACK BANK UPDATE",
                "sepay_sub_account": "VA_PRIMARY",
            },
            headers=auth_headers,
        )
        assert hijack_update.status_code == 409, hijack_update.text
        comp_b_check = (
            await client.get(f"/api/v1/companies/{comp_b['id']}", headers=auth_headers)
        ).json()
        assert comp_b_check["bank_account_number"] == "99445566"
        assert comp_b_check["sepay_sub_account"] == "VA_SECONDARY"
        assert await erp_repository.resolve_channel_context(
            "sepay_account", "99112233", "VA_PRIMARY"
        ) == ("default-tenant", UUID(comp_a["id"]))


@pytest.mark.asyncio
async def test_sec03_and_sec04_unbound_company_scope_and_banks_role_guard(
    auth_headers: dict[str, str],
) -> None:
    """SEC-03 & SEC-04: Unbound manager/staff fail closed (403); GET /companies/banks blocks staff (403)."""
    # SEC-03 unit verification: resolve_company_scope always raises 403 if manager/staff has company_id=None
    for role in ("manager", "staff"):
        unbound_principal = AuthenticatedUser(
            user_id="00000000-0000-0000-0000-000000000099",
            tenant_id="default-tenant",
            role=role,
            email=f"unbound-{role}@test.local",
            jti="unbound-jti",
            permission_group=PermissionGroup.LIVE_API,
            company_id=None,
        )
        with pytest.raises(HTTPException) as exc_info:
            resolve_company_scope(unbound_principal)
        assert exc_info.value.status_code == 403

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # SEC-03 endpoint verification: unbound manager/staff token gets 403 on company-scoped ERP routes
        unbound_token = create_access_token(
            user_id="00000000-0000-0000-0000-000000000098",
            tenant_id="default-tenant",
            role="manager",
            email="unbound-mgr@test.local",
            jti="jti-unbound-mgr",
            permission_group=PermissionGroup.LIVE_API,
            company_id=None,
        )
        await erp_repository.store_token_record(
            user_id="00000000-0000-0000-0000-000000000098",
            jti="jti-unbound-mgr",
            token_hash=hash_token(unbound_token),
            token_type="access",
            expires_at=erp_repository._users[next(iter(erp_repository._users))]["created_at"],
            tenant_id="default-tenant",
        )
        unbound_headers = {"Authorization": f"Bearer {unbound_token}"}
        assert (
            await client.get("/api/v1/erp/orders", headers=unbound_headers)
        ).status_code == 403
        assert (
            await client.get("/api/v1/erp/my-work", headers=unbound_headers)
        ).status_code == 403

        # SEC-04: GET /api/v1/companies/banks returns 200 for admin & manager, 403 for staff
        comp = await _create_company(
            client,
            auth_headers,
            name="Banks Guard Co",
            code="BGRD",
            bank_code="ACB",
            bank_account_number="55112233",
            bank_account_name="BANKS GUARD CO",
        )
        _, mgr_headers = await _provision_active_manager(
            client,
            auth_headers,
            company_id=comp["id"],
            full_name="Guard Mgr",
            username="mgr_bgrd",
            email="mgr@bgrd.local",
        )
        staff = (
            await client.post(
                "/api/v1/erp/staff",
                json={"full_name": "Guard Staff", "telegram_user_id": "456456"},
                headers=mgr_headers,
            )
        ).json()
        s_login = await _signin(
            client,
            identifier=staff["username"],
            password=staff["temporary_password"],
        )
        s_cp = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": staff["temporary_password"],
                "new_password": "StaffGuardPass!2026",
            },
            headers={"Authorization": f"Bearer {s_login.json()['access_token']}"},
        )
        staff_headers = {"Authorization": f"Bearer {s_cp.json()['access_token']}"}

        assert (await client.get("/api/v1/companies/banks", headers=auth_headers)).status_code == 200
        assert (await client.get("/api/v1/companies/banks", headers=mgr_headers)).status_code == 200
        assert (await client.get("/api/v1/companies/banks", headers=staff_headers)).status_code == 403


def test_conftest_postgres_truncate_includes_company_and_chat_tables() -> None:
    """Verify conftest.reset_postgres_test_state includes companies, channel_conversations, and channel_messages."""
    import inspect

    source = inspect.getsource(test_conftest.reset_postgres_test_state)
    for table_name in ("companies", "channel_conversations", "channel_messages"):
        assert table_name in source, f"{table_name} missing from reset_postgres_test_state"

