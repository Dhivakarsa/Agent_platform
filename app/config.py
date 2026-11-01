"""
Application configuration using Pydantic BaseSettings.

Loads configuration from environment variables and .env file.
Validates secrets, timeouts, database parameters, and bootstrap account credentials.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

_BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BASE_DIR / ".env")


class Settings(BaseSettings):
    """Central configuration for the agentic platform."""

    # ── Application ──────────────────────────────────────────────────────
    app_name: str = "Datazoic Agentic System"
    app_env: Literal["local", "development", "staging", "production"] = "local"
    debug: bool = True
    log_level: str = "INFO"

    # ── Security & Authentication ────────────────────────────────────────
    app_secret_key: str = Field(
        default="local-dev-secret-key-32-chars-long-at-least",
        description="Application encryption secret",
    )
    jwt_secret_key: str = Field(
        default="local-jwt-secret-key-32-chars-long-at-least",
        description="HMAC secret for signing JWT tokens",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours for local dev session

    # ── Initial Local Demo Accounts (Bootstrapped securely into DB) ──────
    bootstrap_admin_email: str = "admin@examplemail.com"
    bootstrap_admin_password: str = "admin@123"
    bootstrap_user_email: str = "user@examplemail.com"
    bootstrap_user_password: str = "user@123"

    # ── Gemini / Google GenAI ────────────────────────────────────────────
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    gemini_model: str = "gemini-2.5-flash"
    embedding_model: str = "text-embedding-004"
    embedding_dimension: int = 768

    # ── Database ─────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./agentic_system.db"
    database_echo: bool = False

    # ── Qdrant ───────────────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection_name: str = "tool_index"
    qdrant_knowledge_collection: str = "knowledge_base"
    qdrant_use_memory: bool = True

    # ── Redis (optional) ─────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── PayPal Sandbox ───────────────────────────────────────────────────
    paypal_mode: Literal["sandbox", "live"] = "sandbox"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"

    # ── SMTP Outbound Email ──────────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # ── Execution Engine & Agent Guardrails ──────────────────────────────
    execution_mode: Literal["mock", "sandbox", "live"] = "mock"
    default_api_timeout: int = 30
    max_agent_iterations: int = 10
    max_tool_retries: int = 3
    retrieval_top_k: int = 6
    dense_weight: float = 0.6
    keyword_weight: float = 0.4

    # ── Compatibility Aliases ────────────────────────────────────────────
    @property
    def DATABASE_URL(self) -> str:
        return self.database_url

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def GEMINI_API_KEY(self) -> str:
        return self.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Return a singleton settings instance."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


settings = get_settings()
