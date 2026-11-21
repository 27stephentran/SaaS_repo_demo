"""Comprehensive v2 upgrade QA tests: serializer audit, permission groups, soft-delete/retention, and zero hardcoded defaults."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, get_args, get_origin
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.database import erp_repository
from app.main import app
from app.models.entities import CustomerResponse, EmployeeResponse, InventoryCreateRequest
from app.services.retention_service import retention_service

ALLOWLISTED_UNAUTH_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/api/v1/webhooks/meta"),
        ("POST", "/api/v1/webhooks/meta"),
        ("POST", "/api/v1/webhooks/sepay"),
        ("POST", "/api/v1/auth/token"),
        ("POST", "/api/v1/auth/signin"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/signup"),
        ("POST", "/api/v1/auth/demo-signin"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/auth/token"),
        ("POST", "/auth/signin"),
        ("POST", "/auth/login"),
        ("POST", "/auth/signup"),
        ("POST", "/auth/demo-signin"),
        ("POST", "/auth/refresh"),
    }
)


def _contains_pydantic_model(annotation: Any) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True
    origin = get_origin(annotation)
    if origin is not None:
        return any(_contains_pydantic_model(arg) for arg in get_args(annotation))
    return False


def _materialize_route_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(uuid4()), path)


def _collect_api_routes() -> list[Any]:
    collected: list[Any] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            collected.append(route)
        elif hasattr(route, "effective_route_contexts"):
            collected.extend(route.effective_route_contexts())
    return collected


# ------------------------------------------------------------------------------
# 1. Serializer Audit
# ------------------------------------------------------------------------------


def test_v2_every_json_endpoint_defines_pydantic_response_model() -> None:
    """Introspect `app.routes` and assert every JSON endpoint defines a Pydantic `response_model`."""
    api_routes = _collect_api_routes()
    assert len(api_routes) >= 20

    for route in api_routes:
        if route.path == "/api/v1/webhooks/meta" and "GET" in route.methods:
            continue
        assert route.response_model is not None, (
            f"Route {sorted(route.methods)} {route.path} is missing response_model"
        )
        assert _contains_pydantic_model(route.response_model), (
            f"Route {sorted(route.methods)} {route.path} response_model "
            f"{route.response_model!r} is not backed by a Pydantic BaseModel"
        )


# ------------------------------------------------------------------------------
# 2. Auth, Signup/Signin/Demo-Signin & Permission Group Isolation
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_all_non_allowlisted_routes_fail_closed_401_without_auth() -> None:
    """Every non-allowlisted route fails closed with HTTP 401 when Authorization header is omitted."""
    api_routes = _collect_api_routes()
    checked = 0

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for route in api_routes:
            for method in sorted(route.methods):
                if (method, route.path) in ALLOWLISTED_UNAUTH_ROUTES:
                    continue
                target_path = _materialize_route_path(route.path)
                resp = await client.request(method, target_path)
                assert resp.status_code == 401, (
                    f"Expected 401 for unauthenticated {method} {route.path}, "
                    f"got {resp.status_code}: {resp.text}"
                )
                checked += 1

    assert checked >= 20


@pytest.mark.asyncio
async def test_v2_signup_signin_demo_signin_and_permission_group_isolation(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: dict[str, str],
) -> None:
    """Verify `/signup`, `/signin`, `/demo-signin`, and strict `live_api` vs `demo_api` isolation."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Self-serve signup on brand-new tenant -> 201 admin live_api token pair
        signup_resp = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "founder@brand-new-tenant.test",
                "password": "StrongSignupPass!123",
                "tenant_id": "brand-new-tenant",
            },
        )
        assert signup_resp.status_code == 201
        signup_pair = signup_resp.json()
        assert signup_pair["permission_group"] == "live_api"
        assert signup_pair["role"] == "admin"
        assert signup_pair["tenant_id"] == "brand-new-tenant"

        # Joining an already-existing tenant or demo-tenant via unauthenticated signup -> 403
        existing_tenant_signup = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "attacker@brand-new-tenant.test",
                "password": "StrongSignupPass!123",
                "tenant_id": "brand-new-tenant",
            },
        )
        assert existing_tenant_signup.status_code == 403

        demo_tenant_signup = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "attacker@demo-tenant.test",
                "password": "StrongSignupPass!123",
                "tenant_id": "demo-tenant",
            },
        )
        assert demo_tenant_signup.status_code == 403

        # 2. Signin with signed-up operator credentials -> 200 live_api
        signin_resp = await client.post(
            "/api/v1/auth/signin",
            json={
                "email": "founder@brand-new-tenant.test",
                "password": "StrongSignupPass!123",
                "tenant_id": "brand-new-tenant",
            },
        )
        assert signin_resp.status_code == 200
        live_pair = signin_resp.json()
        assert live_pair["permission_group"] == "live_api"
        assert live_pair["role"] == "admin"
        live_headers = {"Authorization": f"Bearer {live_pair['access_token']}"}

        live_me = await client.get("/api/v1/auth/me", headers=live_headers)
        assert live_me.status_code == 200
        assert live_me.json()["permission_group"] == "live_api"

        # 3. Demo sign-in -> 200 demo_api token pair; refresh preserves demo_api
        demo_resp = await client.post("/api/v1/auth/demo-signin")
        assert demo_resp.status_code == 200
        demo_pair = demo_resp.json()
        assert demo_pair["permission_group"] == "demo_api"
        assert demo_pair["role"] == "demo"
        assert demo_pair["tenant_id"] == "demo-tenant"
        demo_headers = {"Authorization": f"Bearer {demo_pair['access_token']}"}

        demo_me = await client.get("/api/v1/auth/me", headers=demo_headers)
        assert demo_me.status_code == 200
        assert demo_me.json()["permission_group"] == "demo_api"

        demo_refresh = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": demo_pair["refresh_token"]},
        )
        assert demo_refresh.status_code == 200
        assert demo_refresh.json()["permission_group"] == "demo_api"
        assert demo_refresh.json()["role"] == "demo"

        # Demo user cannot sign in via live /signin
        demo_live_signin = await client.post(
            "/api/v1/auth/signin",
            json={
                "email": "demo-sandbox@demo-tenant.local",
                "password": "wrong-password",
                "tenant_id": "demo-tenant",
            },
        )
        assert demo_live_signin.status_code == 401

        # 4. demo_api tokens receive 403 on EVERY /api/v1/erp/* and /api/v1/system/* route
        api_routes = _collect_api_routes()
        guarded_routes = [
            (method, route.path)
            for route in api_routes
            if route.path.startswith("/api/v1/erp") or route.path.startswith("/api/v1/system")
            for method in sorted(route.methods)
        ]
        assert len(guarded_routes) >= 16
        for method, path in guarded_routes:
            resp = await client.request(
                method,
                _materialize_route_path(path),
                headers=demo_headers,
            )
            assert resp.status_code == 403, (
                f"Expected 403 for demo_api on {method} {path}, got {resp.status_code}"
            )

        # 5. demo_api token succeeds on POST /api/v1/demo/notify-telegram (simulated preview when unset)
        demo_notify_ok = await client.post(
            "/api/v1/demo/notify-telegram",
            json={
                "order_code": "DEMO-1001",
                "total_amount_vnd": 320000,
                "items_summary": "1x COFFEE-ROBUSTA-1KG",
                "customer_name": "Demo Shopper",
                "telegram_user_id": "8499887766",
            },
            headers=demo_headers,
        )
        assert demo_notify_ok.status_code == 200
        demo_notify_data = demo_notify_ok.json()
        assert demo_notify_data["dispatched"] is True
        assert demo_notify_data["simulated"] is True
        assert demo_notify_data["order_code"] == "DEMO-1001"
        assert demo_notify_data["telegram_user_id"] == "8499887766"
        assert demo_notify_data["preview_text"]

        # 6. live_api token receives 403 on /api/v1/demo/*
        live_on_demo = await client.post(
            "/api/v1/demo/notify-telegram",
            json={
                "order_code": "DEMO-1001",
                "total_amount_vnd": 320000,
                "items_summary": "1x COFFEE-ROBUSTA-1KG",
                "telegram_user_id": "8499887766",
            },
            headers=live_headers,
        )
        assert live_on_demo.status_code == 403

        # 7. live_api token succeeds on live order Telegram notification
        seeded_order = erp_repository.list_orders("default-tenant")[0]
        live_order_notify = await client.post(
            f"/api/v1/erp/orders/{seeded_order.id}/notify-telegram",
            headers=auth_headers,
        )
        assert live_order_notify.status_code == 200
        assert live_order_notify.json()["dispatched"] is True
        assert live_order_notify.json()["order_code"] == seeded_order.order_code

        # 8. Demo notify endpoint enforces rate limiting (429)
        monkeypatch.setenv("DEMO_NOTIFY_RATE_LIMIT_PER_MINUTE", "2")
        get_settings.cache_clear()
        from app.api.demo import demo_rate_limiter

        demo_rate_limiter.reset()
        for _ in range(2):
            r = await client.post(
                "/api/v1/demo/notify-telegram",
                json={
                    "order_code": "DEMO-RL",
                    "total_amount_vnd": 100000,
                    "items_summary": "1x SKU",
                    "telegram_user_id": "8499887766",
                },
                headers=demo_headers,
            )
            assert r.status_code == 200
        rl_resp = await client.post(
            "/api/v1/demo/notify-telegram",
            json={
                "order_code": "DEMO-RL",
                "total_amount_vnd": 100000,
                "items_summary": "1x SKU",
                "telegram_user_id": "8499887766",
            },
            headers=demo_headers,
        )
        assert rl_resp.status_code == 429


# ------------------------------------------------------------------------------
# 3. Soft-Delete, Restore & Retention Cleanup
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_soft_delete_and_restore_inventory_customers_employees(
    auth_headers: dict[str, str],
    tenant_b_headers: dict[str, str],
) -> None:
    """Soft-deleting hides records from default lists & RAG search; restore brings them back."""
    inv_items = erp_repository.list_inventory("default-tenant")
    target_inv = next(i for i in inv_items if i.sku == "EQUIP-V60-KIT")
    target_cust = erp_repository.list_customers("default-tenant")[0]
    target_emp = erp_repository.list_employees("default-tenant")[0]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Cross-tenant delete/restore must fail with 404
        assert (
            await client.delete(
                f"/api/v1/erp/inventory/{target_inv.id}",
                headers=tenant_b_headers,
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/v1/erp/customers/{target_cust.id}/restore",
                headers=tenant_b_headers,
            )
        ).status_code == 404

        # 1. Soft-delete inventory item -> hidden from default list and RAG search
        del_inv = await client.delete(
            f"/api/v1/erp/inventory/{target_inv.id}",
            headers=auth_headers,
        )
        assert del_inv.status_code == 200
        del_inv_body = del_inv.json()
        assert del_inv_body["resource"] == "inventory"
        assert del_inv_body["is_active"] is False
        assert del_inv_body["deactivated_at"] is not None

        active_inv = (await client.get("/api/v1/erp/inventory", headers=auth_headers)).json()
        assert all(item["id"] != str(target_inv.id) for item in active_inv)

        all_inv = (
            await client.get(
                "/api/v1/erp/inventory",
                params={"include_inactive": "true"},
                headers=auth_headers,
            )
        ).json()
        archived_inv = next(item for item in all_inv if item["id"] == str(target_inv.id))
        assert archived_inv["is_active"] is False

        rag_post = (
            await client.post(
                "/api/v1/erp/inventory/search",
                json={"query": "Ceramic V60 Pour-Over Brewer Kit", "limit": 10},
                headers=auth_headers,
            )
        ).json()
        assert all(item["id"] != str(target_inv.id) for item in rag_post)

        rag_get = (
            await client.get(
                "/api/v1/erp/inventory/search",
                params={"q": "Ceramic V60 Pour-Over Brewer Kit", "limit": 10},
                headers=auth_headers,
            )
        ).json()
        assert all(item["id"] != str(target_inv.id) for item in rag_get)

        # Restore inventory item
        restore_inv = await client.post(
            f"/api/v1/erp/inventory/{target_inv.id}/restore",
            headers=auth_headers,
        )
        assert restore_inv.status_code == 200
        assert restore_inv.json()["is_active"] is True
        assert restore_inv.json()["deactivated_at"] is None

        restored_inv_list = (
            await client.get("/api/v1/erp/inventory", headers=auth_headers)
        ).json()
        assert any(item["id"] == str(target_inv.id) for item in restored_inv_list)
        rag_after_restore = (
            await client.post(
                "/api/v1/erp/inventory/search",
                json={"query": "Ceramic V60 Pour-Over Brewer Kit", "limit": 10},
                headers=auth_headers,
            )
        ).json()
        assert any(item["id"] == str(target_inv.id) for item in rag_after_restore)

        # 2. Soft-delete and restore customer
        del_cust = await client.delete(
            f"/api/v1/erp/customers/{target_cust.id}",
            headers=auth_headers,
        )
        assert del_cust.status_code == 200
        assert del_cust.json()["is_active"] is False
        assert del_cust.json()["deactivated_at"] is not None

        assert (await client.get("/api/v1/erp/customers", headers=auth_headers)).json() == []
        inactive_custs = (
            await client.get(
                "/api/v1/erp/customers",
                params={"include_inactive": "true"},
                headers=auth_headers,
            )
        ).json()
        assert len(inactive_custs) == 1
        assert inactive_custs[0]["is_active"] is False

        restore_cust = await client.post(
            f"/api/v1/erp/customers/{target_cust.id}/restore",
            headers=auth_headers,
        )
        assert restore_cust.status_code == 200
        assert restore_cust.json()["is_active"] is True
        assert restore_cust.json()["deactivated_at"] is None
        assert len((await client.get("/api/v1/erp/customers", headers=auth_headers)).json()) == 1

        # 3. Soft-delete and restore employee
        del_emp = await client.delete(
            f"/api/v1/erp/employees/{target_emp.id}",
            headers=auth_headers,
        )
        assert del_emp.status_code == 200
        assert del_emp.json()["is_active"] is False
        assert del_emp.json()["deactivated_at"] is not None

        assert (await client.get("/api/v1/erp/employees", headers=auth_headers)).json() == []
        inactive_emps = (
            await client.get(
                "/api/v1/erp/employees",
                params={"include_inactive": "true"},
                headers=auth_headers,
            )
        ).json()
        assert len(inactive_emps) == 1
        assert inactive_emps[0]["is_active"] is False

        restore_emp = await client.post(
            f"/api/v1/erp/employees/{target_emp.id}/restore",
            headers=auth_headers,
        )
        assert restore_emp.status_code == 200
        assert restore_emp.json()["is_active"] is True
        assert restore_emp.json()["deactivated_at"] is None
        assert len((await client.get("/api/v1/erp/employees", headers=auth_headers)).json()) == 1


@pytest.mark.asyncio
async def test_v2_retention_cleanup_window_fk_protection_and_backup_abort(
    auth_headers: dict[str, str],
) -> None:
    """Retention cleanup purges only expired records, respects FK protection, and aborts on exporter error."""
    now = datetime.now(UTC)
    expired_at = now - timedelta(days=400)
    recent_at = now - timedelta(days=10)

    # Create two additional inventory items: one expired soft-delete, one recent soft-delete
    expired_inv = erp_repository.create_inventory_item(
        InventoryCreateRequest(
            tenant_id="default-tenant",
            sku="EXPIRED-SKU-001",
            name="Expired SKU",
            description="Should be purged past 365 days",
            category="Coffee",
            price_vnd=100000,
            stock_quantity=1,
        ),
        embedding=[0.1] * 768,
    )
    recent_inv = erp_repository.create_inventory_item(
        InventoryCreateRequest(
            tenant_id="default-tenant",
            sku="RECENT-SKU-002",
            name="Recent Inactive SKU",
            description="Should NOT be purged within 365 days",
            category="Coffee",
            price_vnd=100000,
            stock_quantity=1,
        ),
        embedding=[0.1] * 768,
    )
    await erp_repository.soft_delete_inventory_item(expired_inv.id, tenant_id="default-tenant")
    erp_repository._inventory[expired_inv.id]["deactivated_at"] = expired_at
    await erp_repository.soft_delete_inventory_item(recent_inv.id, tenant_id="default-tenant")
    erp_repository._inventory[recent_inv.id]["deactivated_at"] = recent_at

    # Customer 1 (seeded, referenced by DH1001) -> expired soft-delete, MUST be FK-protected
    seeded_cust = erp_repository.list_customers("default-tenant")[0]
    await erp_repository.soft_delete_customer(seeded_cust.id, tenant_id="default-tenant")
    erp_repository._customers[seeded_cust.id] = erp_repository._customers[seeded_cust.id].model_copy(
        update={"deactivated_at": expired_at}
    )

    # Customer 2 (unreferenced) -> expired soft-delete, SHOULD be purged
    orphan_cust_id = uuid4()
    erp_repository._customers[orphan_cust_id] = CustomerResponse(
        id=orphan_cust_id,
        tenant_id="default-tenant",
        meta_psid="psid_orphan_expired",
        full_name="Orphan Expired Customer",
        phone=None,
        is_active=False,
        deactivated_at=expired_at,
        created_at=expired_at,
    )

    # Seeded employee (assigned to DH1001) -> expired soft-delete, SHOULD be purged & set order's employee NULL
    seeded_emp = erp_repository.list_employees("default-tenant")[0]
    await erp_repository.soft_delete_employee(seeded_emp.id, tenant_id="default-tenant")
    erp_repository._employees[seeded_emp.id] = erp_repository._employees[seeded_emp.id].model_copy(
        update={"deactivated_at": expired_at}
    )

    # Also add an unrelated active employee in tenant-b to ensure cross-tenant isolation
    tb_emp_id = uuid4()
    erp_repository._employees[tb_emp_id] = EmployeeResponse(
        id=tb_emp_id,
        tenant_id="tenant-b",
        full_name="Tenant B Archived",
        role="staff",
        telegram_user_id="999",
        is_active=False,
        deactivated_at=expired_at,
        created_at=expired_at,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Dry-run previews eligible and FK-protected counts without deleting
        dry_resp = await client.post(
            "/api/v1/system/retention/cleanup",
            json={"dry_run": True},
            headers=auth_headers,
        )
        assert dry_resp.status_code == 200
        dry_data = dry_resp.json()
        assert dry_data["dry_run"] is True
        assert dry_data["backup_status"] == "skipped_dry_run"
        assert dry_data["eligible_counts"] == {"inventory": 1, "customers": 2, "employees": 1}
        assert dry_data["deleted_counts"] == {"inventory": 0, "customers": 0, "employees": 0}
        assert dry_data["skipped_fk_protected_counts"]["customers"] == 1
        assert expired_inv.id in erp_repository._inventory
        assert orphan_cust_id in erp_repository._customers

        # 2. Failing BackupExporter aborts purge before any deletion occurs
        class _FailingExporter:
            provider_name = "mock_failing_exporter"

            async def export_expired_records(
                self,
                *,
                tenant_id: str,
                cutoff_at: datetime,
                records: dict[str, list[dict[str, Any]]],
            ) -> str:
                raise RuntimeError("Backup export failed")

        with pytest.raises(RuntimeError, match="Backup export failed"):
            await retention_service.run_cleanup(
                tenant_id="default-tenant",
                dry_run=False,
                backup_exporter=_FailingExporter(),
            )

        assert expired_inv.id in erp_repository._inventory
        assert orphan_cust_id in erp_repository._customers
        assert seeded_emp.id in erp_repository._employees

        # 3. Live retention cleanup purges only expired non-FK-protected records
        purge_resp = await client.post(
            "/api/v1/system/retention/cleanup",
            json={"dry_run": False},
            headers=auth_headers,
        )
        assert purge_resp.status_code == 200
        purge_data = purge_resp.json()
        assert purge_data["dry_run"] is False
        assert purge_data["backup_status"] == "deferred"
        assert purge_data["deleted_counts"] == {"inventory": 1, "customers": 1, "employees": 1}
        assert purge_data["skipped_fk_protected_counts"]["customers"] == 1

        # Verify post-purge state
        assert expired_inv.id not in erp_repository._inventory
        assert recent_inv.id in erp_repository._inventory
        assert orphan_cust_id not in erp_repository._customers
        assert seeded_cust.id in erp_repository._customers  # FK-protected by DH1001
        assert seeded_emp.id not in erp_repository._employees
        assert tb_emp_id in erp_repository._employees  # Tenant B untouched

        seeded_order = erp_repository._orders[UUID("44444444-4444-4444-4444-444444444401")]
        assert seeded_order.assigned_employee_id is None


# ------------------------------------------------------------------------------
# 4. Zero Hardcoded Defaults Audit
# ------------------------------------------------------------------------------


def test_v2_settings_has_no_hardcoded_tenant_localhost_or_dummy_account_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `Settings` has zero hardcoded `default-tenant`, `localhost`, or dummy account defaults."""
    for var in (
        "DEFAULT_TENANT_ID",
        "DEMO_TENANT_ID",
        "DATABASE_URL",
        "CORS_ORIGINS",
        "SEPAY_ACCOUNT_NUMBER",
        "SEPAY_BANK_CODE",
        "SEPAY_WEBHOOK_SECRET",
        "META_APP_SECRET",
        "META_VERIFY_TOKEN",
        "API_TOKEN_SECRET",
        "DEMO_TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(var, raising=False)

    clean_settings = Settings(_env_file=None)
    assert clean_settings.default_tenant_id == ""
    assert clean_settings.demo_tenant_id == ""
    assert clean_settings.database_url == ""
    assert clean_settings.cors_origins == []
    assert clean_settings.sepay_account_number == ""
    assert clean_settings.sepay_bank_code == ""
    assert clean_settings.demo_telegram_chat_id == ""

    for field_name, field_info in Settings.model_fields.items():
        default_repr = repr(field_info.default)
        assert "default-tenant" not in default_repr, f"{field_name} has hardcoded default-tenant"
        assert "localhost" not in default_repr, f"{field_name} has hardcoded localhost"
        assert "127.0.0.1" not in default_repr, f"{field_name} has hardcoded 127.0.0.1"
        assert "0123456789" not in default_repr, f"{field_name} has hardcoded dummy account"


# ------------------------------------------------------------------------------
# 5. T-01-BACKEND-REWORK-1 Regression Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_rework_retention_cleanup_requires_admin_for_live_purge(
    auth_headers: dict[str, str],
    create_test_user: Any,
) -> None:
    """`POST /api/v1/system/retention/cleanup` rejects non-admin (`staff`) on `dry_run=false` (403) and allows `admin`."""
    await create_test_user(
        email="staff-retention@default-tenant.test",
        password="StaffRetention!123",
        role="staff",
        tenant_id="default-tenant",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        signin_resp = await client.post(
            "/api/v1/auth/signin",
            json={
                "email": "staff-retention@default-tenant.test",
                "password": "StaffRetention!123",
                "tenant_id": "default-tenant",
            },
        )
        assert signin_resp.status_code == 200
        staff_headers = {"Authorization": f"Bearer {signin_resp.json()['access_token']}"}

        # Staff can preview dry_run=true
        staff_dry = await client.post(
            "/api/v1/system/retention/cleanup",
            json={"dry_run": True},
            headers=staff_headers,
        )
        assert staff_dry.status_code == 200

        # Staff cannot execute destructive dry_run=false
        staff_live = await client.post(
            "/api/v1/system/retention/cleanup",
            json={"dry_run": False},
            headers=staff_headers,
        )
        assert staff_live.status_code == 403

        # Admin succeeds on dry_run=false
        admin_live = await client.post(
            "/api/v1/system/retention/cleanup",
            json={"dry_run": False},
            headers=auth_headers,
        )
        assert admin_live.status_code == 200


@pytest.mark.asyncio
async def test_v2_rework_demo_notify_telegram_chat_id_pinning_and_prod_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Demo notify never calls live Telegram with untrusted chat_id when unset, returns 503 in prod, and pins DEMO_TELEGRAM_CHAT_ID."""
    from app.services.integrations import telegram_dispatch_client

    dispatch_calls: list[dict[str, Any]] = []

    async def _spy_dispatch(**kwargs: Any) -> dict[str, Any]:
        dispatch_calls.append(kwargs)
        return {
            "dispatched": True,
            "simulated": False,
            "telegram_user_id": kwargs["telegram_user_id"],
            "order_code": kwargs["order_code"],
        }

    monkeypatch.setattr(telegram_dispatch_client, "bot_token", "987654321:LIVE_BOT_TOKEN")
    monkeypatch.setattr(telegram_dispatch_client, "dispatch_order_alert", _spy_dispatch)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        demo_signin = await client.post("/api/v1/auth/demo-signin")
        assert demo_signin.status_code == 200
        demo_headers = {"Authorization": f"Bearer {demo_signin.json()['access_token']}"}

        # 1. Non-prod with DEMO_TELEGRAM_CHAT_ID unset -> simulated preview, never calls live Telegram API
        monkeypatch.delenv("DEMO_TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("APP_ENV", "development")
        get_settings.cache_clear()

        unset_resp = await client.post(
            "/api/v1/demo/notify-telegram",
            json={
                "order_code": "DEMO-UNSET-01",
                "total_amount_vnd": 250000,
                "items_summary": "1x COFFEE-ROBUSTA-1KG",
                "telegram_user_id": "attacker-arbitrary-chat",
            },
            headers=demo_headers,
        )
        assert unset_resp.status_code == 200
        unset_body = unset_resp.json()
        assert unset_body["simulated"] is True
        assert unset_body["dispatched"] is True
        assert unset_body["preview_text"]
        assert dispatch_calls == []

        # 2. Production with DEMO_TELEGRAM_CHAT_ID unset -> 503 fail closed
        monkeypatch.setenv("APP_ENV", "production")
        get_settings.cache_clear()

        async def _not_revoked(*_args: Any, **_kwargs: Any) -> bool:
            return False

        monkeypatch.setattr(erp_repository, "is_token_revoked", _not_revoked)

        prod_unset_resp = await client.post(
            "/api/v1/demo/notify-telegram",
            json={
                "order_code": "DEMO-PROD-503",
                "total_amount_vnd": 250000,
                "items_summary": "1x COFFEE-ROBUSTA-1KG",
                "telegram_user_id": "attacker-arbitrary-chat",
            },
            headers=demo_headers,
        )
        assert prod_unset_resp.status_code == 503
        assert dispatch_calls == []

        # 3. When DEMO_TELEGRAM_CHAT_ID is configured, target is pinned and ignores client override
        monkeypatch.setenv("DEMO_TELEGRAM_CHAT_ID", "pinned-demo-chat-999")
        get_settings.cache_clear()

        pinned_resp = await client.post(
            "/api/v1/demo/notify-telegram",
            json={
                "order_code": "DEMO-PINNED-02",
                "total_amount_vnd": 320000,
                "items_summary": "1x COFFEE-ROBUSTA-1KG",
                "telegram_user_id": "client-override-attempt-111",
            },
            headers=demo_headers,
        )
        assert pinned_resp.status_code == 200
        pinned_body = pinned_resp.json()
        assert pinned_body["telegram_user_id"] == "pinned-demo-chat-999"
        assert len(dispatch_calls) == 1
        assert dispatch_calls[0]["telegram_user_id"] == "pinned-demo-chat-999"


@pytest.mark.asyncio
async def test_v2_rework_soft_deleted_meta_customer_reactivation_on_webhook(
    auth_headers: dict[str, str],
    meta_signature_headers: Any,
) -> None:
    """Soft-deleted Meta PSID customer is reactivated (`is_active=True`) on inbound webhook and order succeeds."""
    import json

    seeded_customer = erp_repository.list_customers("default-tenant")[0]
    assert seeded_customer.meta_psid == "psid_vn_customer_001"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Soft-delete the Meta customer
        del_resp = await client.delete(
            f"/api/v1/erp/customers/{seeded_customer.id}",
            headers=auth_headers,
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["is_active"] is False
        assert await client.get("/api/v1/erp/customers", headers=auth_headers)
        assert (await client.get("/api/v1/erp/customers", headers=auth_headers)).json() == []

        # 2. Inbound signed Meta webhook from the soft-deleted customer PSID
        meta_payload = {
            "object": "page",
            "entry": [
                {
                    "id": "page_123",
                    "messaging": [
                        {
                            "sender": {"id": "psid_vn_customer_001"},
                            "recipient": {"id": "page_123"},
                            "message": {
                                "mid": "mid_reactivate_001",
                                "text": "Cho mình mua 1 gói Arabica Cau Dat",
                            },
                        }
                    ],
                }
            ],
        }
        raw_body = json.dumps(meta_payload, separators=(",", ":")).encode("utf-8")
        webhook_resp = await client.post(
            "/api/v1/webhooks/meta",
            content=raw_body,
            headers=meta_signature_headers(raw_body, None),
        )
        assert webhook_resp.status_code == 200
        event = webhook_resp.json()["events"][0]
        assert event["order_intent_detected"] is True
        assert event["order_code"] is not None

        # 3. Customer is reactivated and visible again in active customer list
        active_customers = (
            await client.get("/api/v1/erp/customers", headers=auth_headers)
        ).json()
        assert len(active_customers) == 1
        assert active_customers[0]["id"] == str(seeded_customer.id)
        assert active_customers[0]["is_active"] is True
        assert active_customers[0]["deactivated_at"] is None

