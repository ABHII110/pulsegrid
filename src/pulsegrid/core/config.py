"""Application settings loaded from environment variables and an optional ``.env`` file."""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    """Deployment environment the process runs in."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Typed runtime configuration.

    Every field maps to an environment variable prefixed with ``PULSEGRID_``
    (for example ``PULSEGRID_DATABASE_URL``). Secrets are wrapped in ``SecretStr`` so they
    never appear in logs or reprs.
    """

    model_config = SettingsConfigDict(
        env_prefix="PULSEGRID_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = "INFO"
    log_json: bool = True

    database_url: SecretStr = SecretStr("sqlite+aiosqlite:///./pulsegrid.db")
    db_echo: bool = False
    redis_url: SecretStr | None = None

    api_key: SecretStr | None = None
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    # Serve /docs and /openapi.json even in production (the landing page links to them).
    expose_docs: bool = False
    # Run the probe scheduler inside the API process (single-service hosting).
    embedded_worker: bool = False
    # Apply database migrations before `pulsegrid serve` starts accepting requests.
    migrate_on_start: bool = False

    allow_private_targets: bool = False
    probe_user_agent: str = Field(default="PulseGrid/0.1", min_length=1, max_length=200)

    worker_poll_interval_seconds: float = Field(default=5.0, gt=0, le=3600)
    worker_concurrency: int = Field(default=20, ge=1, le=1000)
    worker_batch_size: int = Field(default=200, ge=1, le=10_000)

    webhook_url: HttpUrl | None = None
    webhook_secret: SecretStr | None = None
    webhook_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @field_validator("database_url", mode="before")
    @classmethod
    def _use_async_postgres_driver(cls, value: object) -> object:
        """Accept plain ``postgres://`` URLs (as hosting providers hand out) by adding asyncpg."""
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str):
            for prefix in ("postgres://", "postgresql://"):
                if raw.startswith(prefix):
                    return "postgresql+asyncpg://" + raw.removeprefix(prefix)
        return value

    @field_validator("redis_url", "api_key", "webhook_secret", mode="before")
    @classmethod
    def _blank_secret_is_unset(cls, value: object) -> object:
        """Treat empty strings from ``.env`` files as "not configured"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _blank_url_is_unset(cls, value: object) -> object:
        """Treat an empty webhook URL as "not configured"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("api_key")
    @classmethod
    def _api_key_is_strong(cls, value: SecretStr | None) -> SecretStr | None:
        """Reject API keys too short to resist brute force."""
        if value is not None and len(value.get_secret_value()) < 24:
            msg = "api_key must be at least 24 characters"
            raise ValueError(msg)
        return value

    @property
    def is_sqlite(self) -> bool:
        """Whether the configured database is SQLite."""
        return self.database_url.get_secret_value().startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loading them on first use."""
    return Settings()
