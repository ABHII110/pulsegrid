from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from pulsegrid.core.config import Environment, Settings, get_settings
from tests.factories import make_settings


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.api_key is None
    assert settings.redis_url is None
    assert settings.allow_private_targets is False
    assert settings.api_host == "127.0.0.1"
    assert settings.is_sqlite


def test_reads_prefixed_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULSEGRID_ENVIRONMENT", "production")
    monkeypatch.setenv("PULSEGRID_WORKER_CONCURRENCY", "7")
    monkeypatch.setenv("PULSEGRID_DATABASE_URL", "postgresql+asyncpg://u:p@db/pulsegrid")

    settings = get_settings()

    assert settings.environment is Environment.PRODUCTION
    assert settings.worker_concurrency == 7
    assert not settings.is_sqlite
    assert get_settings() is settings


def test_reads_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PULSEGRID_API_PORT=9001\n", encoding="utf-8")

    assert Settings().api_port == 9001


@pytest.mark.parametrize("field", ["redis_url", "api_key", "webhook_secret", "webhook_url"])
def test_blank_optional_values_mean_unset(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    monkeypatch.setenv(f"PULSEGRID_{field.upper()}", "   ")

    assert getattr(Settings(_env_file=None), field) is None


def test_short_api_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="at least 24 characters"):
        make_settings(tmp_path, api_key=SecretStr("short"))


def test_out_of_range_values_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, worker_concurrency=0)


def test_secrets_are_masked_in_repr(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, webhook_secret=SecretStr("super-secret-value"))

    assert "super-secret-value" not in repr(settings)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres://u:p@db:5432/app", "postgresql+asyncpg://u:p@db:5432/app"),
        ("postgresql://u:p@db/app", "postgresql+asyncpg://u:p@db/app"),
        ("postgresql+asyncpg://u:p@db/app", "postgresql+asyncpg://u:p@db/app"),
        ("sqlite+aiosqlite:////tmp/pulsegrid.db", "sqlite+aiosqlite:////tmp/pulsegrid.db"),
    ],
)
def test_database_url_gets_async_driver(
    monkeypatch: pytest.MonkeyPatch, given: str, expected: str
) -> None:
    monkeypatch.setenv("PULSEGRID_DATABASE_URL", given)

    assert Settings(_env_file=None).database_url.get_secret_value() == expected


def test_hosting_flags_default_off() -> None:
    settings = Settings(_env_file=None)

    assert (settings.expose_docs, settings.embedded_worker, settings.migrate_on_start) == (
        False,
        False,
        False,
    )
