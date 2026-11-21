"""Pydantic Settings configuration for the Omnichannel AI Sales & ERP Backend."""

from __future__ import annotations

import json
import secrets
import sys
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded strictly from environment variables or `.env`."""

    app_name: str = "Omnichannel AI Sales & ERP B2B SaaS API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    default_tenant_id: str = ""
    demo_tenant_id: str = ""

    # Supabase PostgreSQL + pgvector (asyncpg driver)
    database_url: str = ""
    use_postgres_in_tests: bool = False

    # Soft-delete retention window (days before eligible cleanup)
    soft_delete_retention_days: int = Field(default=365, ge=1)
    retention_cleanup_batch_size: int = Field(default=500, ge=1, le=5000)

    # Backend-signed HS256 JWT authentication & token TTLs
    api_token_secret: str = ""
    api_token_issuer: str = "saas-erp-backend"
    api_token_audience: str = "saas-erp-api"
    access_token_ttl_seconds: int = Field(default=900, ge=1)
    refresh_token_ttl_seconds: int = Field(default=604800, ge=1)
    demo_token_ttl_seconds: int = Field(default=1800, ge=1)
    order_payment_ttl_seconds: int = Field(default=1800, ge=1)
    auth_rate_limit_per_minute: int = Field(default=20, ge=1)
    demo_notify_rate_limit_per_minute: int = Field(default=10, ge=1)

    # Google Gemini API (`google-genai` SDK)
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-1.5-flash"
    gemini_pro_model: str = "gemini-1.5-pro"
    gemini_embedding_model: str = "text-embedding-004"

    # Meta Graph API (v18.0+)
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_verify_token: str = ""
    meta_access_token: str = ""
    meta_app_secret: str = ""
    meta_api_version: str = "v18.0"

    # SePay Webhooks & Dynamic VietQR
    sepay_qr_base_url: str = "https://qr.sepay.vn/img"
    sepay_pg_sandbox_url: str = "https://pay-sandbox.sepay.vn/v1/checkout/init"
    sepay_pg_prod_url: str = "https://pay.sepay.vn/v1/checkout/init"
    sepay_api_key: str = ""
    sepay_account_number: str = ""
    sepay_bank_code: str = ""
    sepay_webhook_secret: str = ""
    sepay_auth_mode: str = "both"  # "hmac" | "apikey" | "both"
    sepay_payment_prefix: str = "DH"
    sepay_memo_prefix: str = ""
    sepay_qr_template: str = "compact"
    sepay_timestamp_tolerance_seconds: int = 300
    sepay_strict_account_check: bool = False

    # SePay Payment Gateway
    sepay_pg_merchant_id: str = ""
    sepay_pg_secret_key: str = ""
    sepay_pg_ipn_secret: str = ""
    sepay_pg_env: str = "sandbox"  # "sandbox" | "production"

    # SePay Bank Hub & User API v2
    sepay_bankhub_base_url: str = "https://bankhub-api.sepay.vn"
    sepay_bankhub_client_id: str = ""
    sepay_bankhub_client_secret: str = ""
    sepay_api_v2_base_url: str = "https://userapi.sepay.vn/v2"

    # Telegram Bot API (Staff Dispatch & Demo Sandbox)
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_bot_token: str = ""
    demo_telegram_chat_id: str = ""

    # CORS origins for Nuxt 3 Frontend (strictly no wildcard "*" with allow_credentials=True)
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError("CORS_ORIGINS JSON array is invalid") from exc
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS JSON value must be a list of strings")
                origins = [str(item).strip() for item in parsed if str(item).strip()]
            else:
                origins = [item.strip() for item in raw.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            origins = [str(item).strip() for item in value if str(item).strip()]
        else:
            raise ValueError("CORS_ORIGINS must be a JSON array, comma-separated string, or list")

        if any(origin == "*" for origin in origins):
            raise ValueError("CORS_ORIGINS must not contain wildcard '*' when allow_credentials=True")
        return origins

    @model_validator(mode="after")
    def _validate_security_posture(self) -> Settings:
        self.app_env = self.app_env.strip().lower() or "development"
        secret = self.api_token_secret.strip()

        if secret and len(secret) < 32:
            raise ValueError("API_TOKEN_SECRET must be at least 32 characters when configured")

        if any(origin == "*" for origin in self.cors_origins):
            raise ValueError("CORS_ORIGINS must not contain wildcard '*' when allow_credentials=True")

        if self.app_env == "production":
            if not secret or len(secret) < 32:
                raise ValueError(
                    "API_TOKEN_SECRET must be set to at least 32 characters in production"
                )
            if not self.cors_origins:
                raise ValueError("CORS_ORIGINS must be non-empty in production")
            self.api_token_secret = secret
        else:
            self.api_token_secret = secret if secret else secrets.token_urlsafe(48)

        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()


def is_production_env() -> bool:
    """Evaluate production mode from runtime settings without duplicating env lookups."""
    fresh = Settings()
    return fresh.app_env == "production"


def get_runtime_database_url() -> str:
    """Return the active `DATABASE_URL` configured in runtime environment or `.env`."""
    return Settings().database_url.strip()


def is_test_postgres_enabled() -> bool:
    """Return True when PostgreSQL test execution is explicitly enabled."""
    return Settings().use_postgres_in_tests


def is_running_under_pytest() -> bool:
    """Return True when executing inside a pytest test runner."""
    return "pytest" in sys.modules
