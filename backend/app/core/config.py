from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEV_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://factorybridge:factorybridge@127.0.0.1:55432/factorybridge"
    redis_url: str = "redis://127.0.0.1:6379/0"

    jwt_secret: str = _INSECURE_DEV_SECRET
    jwt_ttl_minutes: int = 30
    jwt_issuer: str = "factorybridge"

    cors_allowed_origins: str = "http://localhost:5173"

    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: str = "./var/storage"
    object_storage_endpoint: str | None = None
    object_storage_bucket: str = "factorybridge-private"
    object_storage_access_key: str | None = None
    object_storage_secret_key: str | None = None
    object_storage_region: str = "auto"

    ai_provider: Literal["fixture", "anthropic"] = "fixture"
    anthropic_api_key: str | None = None
    ai_model: str | None = None
    ai_timeout_seconds: float = 60.0
    ai_max_input_chars: int = 60_000
    ai_max_tokens_per_document: int = 4096
    ai_daily_budget_usd: float = 2.0
    ai_input_cost_per_mtok_usd: float = 3.0
    ai_output_cost_per_mtok_usd: float = 15.0

    # When set, /health/ready and /metrics require header X-Health-Token.
    health_token: str | None = None
    max_upload_bytes: int = 10 * 1024 * 1024
    demo_seed_enabled: bool = True
    log_level: str = "INFO"
    login_rate_limit_per_minute: int = 10
    upload_rate_limit_per_minute: int = 20
    # Number of reverse proxies in front of the API that append to X-Forwarded-For (Render: 1). 0 = use the peer.
    trusted_proxy_hops: int = 0
    # When true, outbox events are dispatched to Celery. Tests run handlers inline instead.
    outbox_dispatch_enabled: bool = True
    job_stale_after_seconds: int = 300
    max_job_attempts: int = 3

    rule_version: str = Field(default="rules-2026.11.1")

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        # Managed providers hand out postgres:// or postgresql:// URLs; the app only ships the psycopg 3 driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _secret_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.environment == "production":
            if self.jwt_secret == _INSECURE_DEV_SECRET:
                raise ValueError("JWT_SECRET must be set to a generated secret in production")
            if "*" in self.cors_allowed_origins:
                raise ValueError("CORS wildcard is not allowed in production")
            if any(not o.startswith("https://") for o in self.cors_origins):
                raise ValueError("Production CORS origins must be https://")
            if (
                "factorybridge:factorybridge@" in self.database_url
                or "127.0.0.1" in self.database_url
                or "localhost" in self.database_url
            ):
                raise ValueError("Production DATABASE_URL must point at managed Postgres with generated credentials")
            if self.storage_backend != "s3":
                raise ValueError("Production requires STORAGE_BACKEND=s3 (private object storage)")
        if self.ai_provider == "anthropic" and not (self.anthropic_api_key and self.ai_model):
            raise ValueError("AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY and AI_MODEL")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
