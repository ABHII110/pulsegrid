"""Builders shared by tests."""

from pathlib import Path

from pydantic import SecretStr

from pulsegrid.core.config import Environment, Settings

TEST_API_KEY = "test-api-key-0123456789abcdef"


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Settings pointing at a throwaway SQLite file, without reading the environment."""
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "database_url": SecretStr(f"sqlite+aiosqlite:///{(tmp_path / 'pulsegrid.db').as_posix()}"),
        "log_json": False,
        "api_key": SecretStr(TEST_API_KEY),
        "allow_private_targets": False,
    }
    values.update(overrides)
    return Settings.model_validate(values)
