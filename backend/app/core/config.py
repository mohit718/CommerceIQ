"""
Centralized application settings.

All configuration is read from environment variables (see .env.example).
Never hardcode secrets here — this file only defines *how* settings are
loaded, not their values.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str

    # Auth
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # App
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"

    # Redis / Celery (wired up in Phase 7)
    redis_url: str = "redis://localhost:6379/0"

    # File storage — 'local' or 's3'. See app/ingestion/storage/__init__.py.
    storage_backend: str = "local"
    local_storage_dir: str = "./storage/uploads"
    s3_bucket: str | None = None
    s3_region: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Cached so we don't re-parse env vars on every import."""
    return Settings()


settings = get_settings()
