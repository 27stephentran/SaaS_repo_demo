"""Pydantic Settings configuration for the Omnichannel AI Sales & ERP Backend."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or `.env`."""

    app_name: str = "Omnichannel AI Sales & ERP B2B SaaS API"
    app_version: str = "0.1.0"
    default_tenant_id: str = "default-tenant"

    # Supabase PostgreSQL + pgvector (asyncpg driver)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/saas_erp_mvp"

    # Google Gemini API (`google-genai` SDK)
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-1.5-flash"
    gemini_pro_model: str = "gemini-1.5-pro"
    gemini_embedding_model: str = "text-embedding-004"

    # Meta Graph API (v18.0+)
    meta_verify_token: str = "saas-meta-webhook-verify-token"
    meta_access_token: str = ""
    meta_api_version: str = "v18.0"

    # SePay Sandbox API (Dynamic VietQR & IPN Webhook)
    sepay_api_key: str = ""
    sepay_account_number: str = "0123456789"
    sepay_bank_code: str = "MB"
    sepay_webhook_secret: str = "sepay-sandbox-secret"

    # Telegram Bot API (Staff Dispatch)
    telegram_bot_token: str = ""

    # CORS origins for Nuxt 3 Frontend
    cors_origins: list[str] = ["http://localhost:3000", "https://localhost:3000", "*"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
