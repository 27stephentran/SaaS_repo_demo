"""FastAPI application entrypoint for the Omnichannel AI Sales & ERP B2B SaaS Backend."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.erp import router as erp_router
from app.api.webhooks import router as webhooks_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Omnichannel AI Sales & ERP B2B SaaS Backend powered by FastAPI, "
        "Supabase PostgreSQL + pgvector (`<->`), Google Gemini (`google-genai`), "
        "Meta Graph API v18.0+, SePay VietQR IPN, and Telegram Bot API."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(erp_router)
app.include_router(webhooks_router)


@app.get("/health", tags=["System"])
async def health_check() -> dict[str, Any]:
    """Liveness and stack readiness probe for Render & Docker Compose."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "stack": {
            "database": "supabase-postgresql-pgvector",
            "ai_sdk": "google-genai",
            "chat_model": settings.gemini_chat_model,
            "pro_model": settings.gemini_pro_model,
            "embedding_model": settings.gemini_embedding_model,
            "meta_api_version": settings.meta_api_version,
            "payment_gateway": "sepay-vietqr",
            "staff_dispatch": "telegram-bot-api",
        },
    }
