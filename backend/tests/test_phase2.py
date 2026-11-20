"""Phase 2 QA regression tests covering T2.1 (Async AI/RAG & VN PII), T2.2 (Integrations Resilience & HTML), and T2.4 (Tenant Telemetry & Lifespan)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.core.database import erp_repository
from app.main import app, lifespan
from app.models.entities import (
    InventoryCreateRequest,
    OrderCreateRequest,
    OrderLineItemRequest,
    OrderStatus,
)
from app.services.ai_rag import (
    RAGConsultationOutput,
    SalesRAGEngine,
    sanitize_untrusted_input,
)
from app.services.integrations import (
    close_integration_clients,
    meta_graph_client,
    telegram_dispatch_client,
)
from app.services.order_service import order_service

# ------------------------------------------------------------------------------
# T2.1: Async AI/RAG, Vector Space Invariant & VN PII Sanitization
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t21_sales_rag_uses_async_aio_client_and_never_sync_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `SalesRAGEngine.embed_text` and `generate_sales_reply` invoke `.aio.models.*` and never `.models.*`."""
    monkeypatch.setenv("APP_ENV", "development")

    embed_calls: list[dict[str, Any]] = []
    generate_calls: list[dict[str, Any]] = []
    expected_vec = [0.25] * 768

    class _ForbiddenSyncModels:
        def embed_content(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("Sync client.models.embed_content must never be called")

        def generate_content(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("Sync client.models.generate_content must never be called")

    class _AsyncAioModels:
        async def embed_content(self, **kwargs: Any) -> Any:
            embed_calls.append(kwargs)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=expected_vec)])

        async def generate_content(self, **kwargs: Any) -> Any:
            generate_calls.append(kwargs)
            structured = RAGConsultationOutput(
                reply_text="Dạ shop đã chốt đơn 2 gói Arabica Cầu Đất cho anh/chị ạ.",
                model_used=str(kwargs["model"]),
                order_intent_detected=True,
                recommended_sku="COFFEE-ARABICA-500G",
                recommended_quantity=2,
                confidence=0.96,
                requires_confirmation=False,
                prompt_injection_blocked=False,
                retrieved_sku_count=1,
            )
            return SimpleNamespace(parsed=structured, text=structured.model_dump_json())

    engine = SalesRAGEngine(api_key="")
    engine._client = SimpleNamespace(  # type: ignore[assignment]
        models=_ForbiddenSyncModels(),
        aio=SimpleNamespace(models=_AsyncAioModels()),
    )

    vec = await engine.embed_text("Specialty Arabica Cau Dat")
    assert vec == expected_vec
    assert len(embed_calls) == 1
    assert embed_calls[0]["model"] == engine.embedding_model

    items = erp_repository.list_inventory("default-tenant")
    reply = await engine.generate_sales_reply(
        customer_message="Chốt đơn mua 2 gói Arabica Cau Dat",
        matched_items=items,
        tenant_id="default-tenant",
    )
    assert len(generate_calls) == 1
    assert reply["order_intent_detected"] is True
    assert reply["recommended_quantity"] == 2
    assert reply["confidence"] == 0.96
    assert reply["requires_confirmation"] is False
    assert reply["recommended_sku"] == "COFFEE-ARABICA-500G"


@pytest.mark.asyncio
async def test_t21_production_forbids_deterministic_embedding_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """In `APP_ENV=production`, missing Gemini key or embed error raises RuntimeError and never falls back to SHA-256."""
    monkeypatch.setenv("APP_ENV", "production")
    secret_gemini_key = "AIzaTestProductionSecretKey1234567890"

    no_key_engine = SalesRAGEngine(api_key="")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await no_key_engine.embed_text("Arabica coffee")

    with pytest.raises(RuntimeError, match="embed_text_sync is disabled in production"):
        no_key_engine.embed_text_sync("Arabica coffee")

    with pytest.raises(RuntimeError, match="forbidden in production"):
        no_key_engine._deterministic_embedding("Arabica coffee")

    class _FailingAioModels:
        async def embed_content(self, **_kwargs: Any) -> Any:
            raise ConnectionError(f"upstream error key={secret_gemini_key}")

    prod_engine = SalesRAGEngine(api_key="")
    prod_engine.api_key = secret_gemini_key
    prod_engine._client = SimpleNamespace(  # type: ignore[assignment]
        aio=SimpleNamespace(models=_FailingAioModels()),
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="Gemini embedding request failed in production"):
            await prod_engine.embed_text("Arabica coffee")

    assert secret_gemini_key not in caplog.text


@pytest.mark.asyncio
async def test_t21_rag_confirmation_gating_vague_vs_explicit_order() -> None:
    """Verify `RAGConsultationOutput` confirmation gate separates vague inquiries from explicit orders."""
    engine = SalesRAGEngine(api_key="")
    items = erp_repository.list_inventory("default-tenant")

    vague_reply = await engine.generate_sales_reply(
        customer_message="Cho mình hỏi giá cà phê Arabica bao nhiêu ạ?",
        matched_items=items,
        tenant_id="default-tenant",
    )
    validated_vague = RAGConsultationOutput.model_validate(vague_reply)
    assert validated_vague.order_intent_detected is False
    assert validated_vague.requires_confirmation is True
    assert validated_vague.confidence < 0.8

    explicit_reply = await engine.generate_sales_reply(
        customer_message="Chốt đơn mua 3 gói Arabica Cau Dat",
        matched_items=items,
        tenant_id="default-tenant",
    )
    validated_explicit = RAGConsultationOutput.model_validate(explicit_reply)
    assert validated_explicit.order_intent_detected is True
    assert validated_explicit.requires_confirmation is False
    assert validated_explicit.recommended_quantity == 3
    assert validated_explicit.confidence >= 0.8

    # Structured LLM output with low confidence or requires_confirmation=True must stay gated
    class _LowConfidenceAioModels:
        async def generate_content(self, **kwargs: Any) -> Any:
            payload = RAGConsultationOutput(
                reply_text="Anh/chị muốn đặt sản phẩm này phải không ạ?",
                model_used=str(kwargs["model"]),
                order_intent_detected=True,
                recommended_sku="COFFEE-ROBUSTA-1KG",
                recommended_quantity=2,
                confidence=0.65,
                requires_confirmation=False,
                prompt_injection_blocked=False,
                retrieved_sku_count=len(items),
            )
            return SimpleNamespace(parsed=payload, text=payload.model_dump_json())

    gated_engine = SalesRAGEngine(api_key="")
    gated_engine._client = SimpleNamespace(  # type: ignore[assignment]
        aio=SimpleNamespace(models=_LowConfidenceAioModels()),
    )
    gated_reply = await gated_engine.generate_sales_reply(
        customer_message="Có thể mình lấy cà phê",
        matched_items=items,
        tenant_id="default-tenant",
    )
    assert gated_reply["order_intent_detected"] is False
    assert gated_reply["requires_confirmation"] is True


def test_t21_vn_pii_sanitization() -> None:
    """Verify `sanitize_untrusted_input` redacts VN mobile numbers, CCCD/CMND, and bank accounts."""
    raw_msg = (
        "Liên hệ 0901234567 hoặc +84 912 345 678, "
        "CCCD 079099001234, CMND: 123456789, "
        "STK ngân hàng: 1903456789012"
    )
    sanitized, is_injected = sanitize_untrusted_input(raw_msg)
    assert is_injected is False
    assert "[REDACTED_PHONE]" in sanitized
    assert "[REDACTED_ID]" in sanitized
    assert "[REDACTED_BANK_ACCOUNT]" in sanitized
    assert "0901234567" not in sanitized
    assert "912 345 678" not in sanitized
    assert "079099001234" not in sanitized
    assert "123456789" not in sanitized
    assert "1903456789012" not in sanitized


# ------------------------------------------------------------------------------
# T2.2: Integrations Resilience, Retry Policy & HTML Escaping
# ------------------------------------------------------------------------------


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("no_proxy", "NO_PROXY", "http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.asyncio
async def test_t22_telegram_html_escaping_and_parse_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `TelegramDispatchClient.dispatch_order_alert` uses HTML parse_mode and escapes `<script>`, `&`, `>`."""
    monkeypatch.setattr(telegram_dispatch_client, "bot_token", "")
    res = await telegram_dispatch_client.dispatch_order_alert(
        telegram_user_id="8499887766",
        order_code="DH<1001>",
        total_amount_vnd=640000,
        items_summary="1x <script>alert(1)</script> & Beans > All",
        customer_name="Nguyen <Anh> & Co",
        sepay_reference="FT&123>4",
    )
    assert res["dispatched"] is True
    text_body = res["text"]
    assert "<script>" not in text_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text_body
    assert "&amp; Beans &gt; All" in text_body
    assert "Nguyen &lt;Anh&gt; &amp; Co" in text_body
    assert "DH&lt;1001&gt;" in text_body
    assert "FT&amp;123&gt;4" in text_body

    # Also verify live payload sent to Telegram Bot API sets parse_mode="HTML"
    _clear_proxy_env(monkeypatch)
    await close_integration_clients()
    monkeypatch.setattr(telegram_dispatch_client, "bot_token", "987654321:LIVE_HTML_TEST_TOKEN")
    sent_payloads: list[dict[str, Any]] = []

    async def _mock_post(_self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        sent_payloads.append(dict(kwargs.get("json") or {}))
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post)
    try:
        live_res = await telegram_dispatch_client.dispatch_order_alert(
            telegram_user_id="8499887766",
            order_code="DH1001",
            total_amount_vnd=640000,
            items_summary="2x <img src=x onerror=alert(1)> & Tea > Coffee",
            customer_name="Khach <VIP>",
        )
        assert live_res["dispatched"] is True
        assert len(sent_payloads) == 1
        assert sent_payloads[0]["parse_mode"] == "HTML"
        assert "<img" not in sent_payloads[0]["text"]
        assert "&lt;img src=x onerror=alert(1)&gt; &amp; Tea &gt; Coffee" in sent_payloads[0]["text"]
        assert "Khach &lt;VIP&gt;" in sent_payloads[0]["text"]
    finally:
        await close_integration_clients()


@pytest.mark.asyncio
async def test_t22_tenacity_retries_transient_errors_and_never_retries_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify tenacity retries 503/429 and TransportError (succeeding within 3 attempts) while 400/401 do not retry."""
    _clear_proxy_env(monkeypatch)
    await close_integration_clients()

    # Eliminate backoff sleep during unit test execution
    async def _noop_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    monkeypatch.setattr(telegram_dispatch_client, "bot_token", "987654321:RETRY_TEST_TOKEN")
    monkeypatch.setattr(meta_graph_client, "access_token", "EAAB_RETRY_TEST_TOKEN")

    try:
        # 1. Transient 503 -> 429 -> 200 succeeds on attempt 3
        tg_attempts = 0

        async def _tg_retry_post(
            _self: httpx.AsyncClient, url: str, **_kwargs: Any
        ) -> httpx.Response:
            nonlocal tg_attempts
            tg_attempts += 1
            req = httpx.Request("POST", url)
            if tg_attempts == 1:
                return httpx.Response(503, json={"ok": False}, request=req)
            if tg_attempts == 2:
                return httpx.Response(429, json={"ok": False}, request=req)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}}, request=req)

        monkeypatch.setattr(httpx.AsyncClient, "post", _tg_retry_post)
        tg_res = await telegram_dispatch_client.dispatch_order_alert(
            telegram_user_id="8499887766",
            order_code="DH1001",
            total_amount_vnd=640000,
            items_summary="2x COFFEE-ROBUSTA-1KG",
        )
        assert tg_attempts == 3
        assert tg_res["dispatched"] is True

        # 2. httpx.TransportError -> 200 succeeds on attempt 2 for MetaGraphClient
        meta_attempts = 0

        async def _meta_transport_post(
            _self: httpx.AsyncClient, url: str, **_kwargs: Any
        ) -> httpx.Response:
            nonlocal meta_attempts
            meta_attempts += 1
            req = httpx.Request("POST", url)
            if meta_attempts == 1:
                raise httpx.ConnectError("transient network drop", request=req)
            return httpx.Response(200, json={"message_id": "mid_retry_ok"}, request=req)

        monkeypatch.setattr(httpx.AsyncClient, "post", _meta_transport_post)
        meta_res = await meta_graph_client.send_message(recipient_psid="psid_1", text="Xin chao")
        assert meta_attempts == 2
        assert meta_res["message_id"] == "mid_retry_ok"

        # 3. Non-retryable 400 and 401 never retry (attempts == 1)
        for bad_status in (400, 401):
            tg_4xx_attempts = 0

            async def _post_4xx(
                _self: httpx.AsyncClient,
                url: str,
                _code: int = bad_status,
                **_kwargs: Any,
            ) -> httpx.Response:
                nonlocal tg_4xx_attempts
                tg_4xx_attempts += 1
                return httpx.Response(_code, json={"error": "bad"}, request=httpx.Request("POST", url))

            monkeypatch.setattr(httpx.AsyncClient, "post", _post_4xx)
            fail_tg = await telegram_dispatch_client.dispatch_order_alert(
                telegram_user_id="8499887766",
                order_code="DH1001",
                total_amount_vnd=640000,
                items_summary="1x SKU",
            )
            assert tg_4xx_attempts == 1
            assert fail_tg["dispatched"] is False
            assert fail_tg["status_code"] == bad_status

            meta_4xx_attempts = 0

            async def _meta_post_4xx(
                _self: httpx.AsyncClient,
                url: str,
                _code: int = bad_status,
                **_kwargs: Any,
            ) -> httpx.Response:
                nonlocal meta_4xx_attempts
                meta_4xx_attempts += 1
                return httpx.Response(_code, json={"error": "bad"}, request=httpx.Request("POST", url))

            monkeypatch.setattr(httpx.AsyncClient, "post", _meta_post_4xx)
            fail_meta = await meta_graph_client.send_private_reply(
                comment_id="c_1", text="Hello"
            )
            assert meta_4xx_attempts == 1
            assert fail_meta["delivered"] is False
            assert fail_meta["status_code"] == bad_status
    finally:
        await close_integration_clients()


@pytest.mark.asyncio
async def test_t22_close_integration_clients_and_lifespan_shutdown_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify `close_integration_clients()` and FastAPI `lifespan` close pooled clients cleanly without leaking secrets."""
    _clear_proxy_env(monkeypatch)
    await close_integration_clients()

    meta_secret = "EAAB_LIFESPAN_SECRET_TOKEN"
    tg_secret = "987654321:LIFESPAN_SECRET_TOKEN"
    monkeypatch.setattr(meta_graph_client, "access_token", meta_secret)
    monkeypatch.setattr(telegram_dispatch_client, "bot_token", tg_secret)

    with caplog.at_level(logging.DEBUG):
        async with lifespan(app):
            meta_pool = await meta_graph_client._get_client()
            tg_pool = await telegram_dispatch_client._get_client()
            assert meta_pool.is_closed is False
            assert tg_pool.is_closed is False

        assert meta_graph_client._client is None
        assert telegram_dispatch_client._client is None
        assert meta_pool.is_closed is True
        assert tg_pool.is_closed is True

        # Idempotent second close call succeeds cleanly
        await close_integration_clients()
        assert meta_graph_client._client is None
        assert telegram_dispatch_client._client is None

    assert meta_secret not in caplog.text
    assert tg_secret not in caplog.text


# ------------------------------------------------------------------------------
# T2.4: Tenant Telemetry Endpoints (`/webhooks/logs` & `/metrics`)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t24_telemetry_endpoints_auth_tenant_isolation_and_counts(
    auth_headers: dict[str, str],
    tenant_b_headers: dict[str, str],
) -> None:
    """Verify `/api/v1/erp/webhooks/logs` & `/metrics` enforce auth (401), reject cross-tenant (403), and return tenant-scoped data."""
    # Seed distinct tenant-b inventory, customer, orders, and webhook logs
    await order_service.create_inventory_item(
        payload=InventoryCreateRequest(
            tenant_id="tenant-b",
            sku="TB-ROBUSTA-500G",
            name="Tenant B Robusta 500g",
            description="Tenant B exclusive coffee",
            category="Coffee",
            price_vnd=180000,
            stock_quantity=20,
        ),
        embedding=[0.2] * 768,
        tenant_id="tenant-b",
    )
    tb_customer = erp_repository.get_or_create_customer_by_psid(
        meta_psid="psid_t24_tb",
        full_name="Tenant B Customer",
        tenant_id="tenant-b",
    )
    tb_order = await order_service.create_order(
        payload=OrderCreateRequest(
            tenant_id="tenant-b",
            customer_id=tb_customer.id,
            items=[OrderLineItemRequest(sku="TB-ROBUSTA-500G", quantity=1)],
        ),
        tenant_id="tenant-b",
    )
    await order_service.update_order_status(
        order_id=tb_order.id,
        new_status=OrderStatus.PAID,
        tenant_id="tenant-b",
    )

    # Record SePay webhook logs across default-tenant and tenant-b with distinct timestamps
    now = datetime.now(UTC)
    erp_repository.record_sepay_webhook_log(
        transaction_id="tx-default-old",
        order_code="DH1001",
        transfer_type="in",
        transfer_amount=640000,
        processing_status="UNDERPAID",
        raw_payload={"id": "tx-default-old"},
        tenant_id="default-tenant",
    )
    erp_repository._sepay_webhook_logs["tx-default-old"]["created_at"] = now - timedelta(minutes=5)

    erp_repository.record_sepay_webhook_log(
        transaction_id="tx-default-new",
        order_code="DH1001",
        transfer_type="in",
        transfer_amount=640000,
        processing_status="PROCESSED",
        raw_payload={"id": "tx-default-new"},
        tenant_id="default-tenant",
    )
    erp_repository._sepay_webhook_logs["tx-default-new"]["created_at"] = now

    erp_repository.record_sepay_webhook_log(
        transaction_id="tx-tenant-b-1",
        order_code=tb_order.order_code,
        transfer_type="in",
        transfer_amount=180000,
        processing_status="PROCESSED",
        raw_payload={"id": "tx-tenant-b-1"},
        tenant_id="tenant-b",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing Bearer JWT -> 401
        for path in ("/api/v1/erp/webhooks/logs", "/api/v1/erp/metrics"):
            unauth = await client.get(path)
            assert unauth.status_code == 401

        # 2. Cross-tenant X-Tenant-Id override -> 403
        for path in ("/api/v1/erp/webhooks/logs", "/api/v1/erp/metrics"):
            cross_header = await client.get(
                path,
                headers={**auth_headers, "X-Tenant-Id": "tenant-b"},
            )
            assert cross_header.status_code == 403

            cross_query = await client.get(
                path,
                params={"tenant_id": "tenant-b"},
                headers=auth_headers,
            )
            assert cross_query.status_code == 403

        # 3. Tenant-scoped webhook logs (newest-first, no cross-tenant bleed)
        default_logs_resp = await client.get("/api/v1/erp/webhooks/logs", headers=auth_headers)
        assert default_logs_resp.status_code == 200
        default_logs = default_logs_resp.json()
        assert [entry["sepay_transaction_id"] for entry in default_logs] == [
            "tx-default-new",
            "tx-default-old",
        ]
        assert all(entry["tenant_id"] == "default-tenant" for entry in default_logs)

        tb_logs_resp = await client.get("/api/v1/erp/webhooks/logs", headers=tenant_b_headers)
        assert tb_logs_resp.status_code == 200
        tb_logs = tb_logs_resp.json()
        assert [entry["sepay_transaction_id"] for entry in tb_logs] == ["tx-tenant-b-1"]
        assert tb_logs[0]["tenant_id"] == "tenant-b"

        # 4. Tenant-scoped ERP KPI metrics
        default_metrics_resp = await client.get("/api/v1/erp/metrics", headers=auth_headers)
        assert default_metrics_resp.status_code == 200
        default_metrics = default_metrics_resp.json()
        assert default_metrics["tenant_id"] == "default-tenant"
        assert default_metrics["active_inventory_skus"] == 3
        assert default_metrics["vectorized_skus"] == 0
        assert default_metrics["pending_vietqr_orders"] == 1
        assert default_metrics["paid_and_dispatched_orders"] == 0
        assert default_metrics["total_orders"] == 1
        assert default_metrics["webhook_events_count"] == 2
        assert default_metrics["orders_by_status"]["PENDING_PAYMENT"] == 1

        tb_metrics_resp = await client.get("/api/v1/erp/metrics", headers=tenant_b_headers)
        assert tb_metrics_resp.status_code == 200
        tb_metrics = tb_metrics_resp.json()
        assert tb_metrics["tenant_id"] == "tenant-b"
        assert tb_metrics["active_inventory_skus"] == 1
        assert tb_metrics["vectorized_skus"] == 1
        assert tb_metrics["pending_vietqr_orders"] == 0
        assert tb_metrics["paid_and_dispatched_orders"] == 1
        assert tb_metrics["total_orders"] == 1
        assert tb_metrics["webhook_events_count"] == 1
        assert json.loads(tb_metrics_resp.text)["orders_by_status"]["PAID"] == 1
