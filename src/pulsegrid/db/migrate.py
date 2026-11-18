"""Programmatic Alembic entry points used by the CLI and tests."""

from alembic import command
from alembic.config import Config

from pulsegrid.core.config import Settings

SCRIPT_LOCATION = "pulsegrid:migrations"


def alembic_config(settings: Settings) -> Config:
    """Build an Alembic configuration pointing at the packaged migration scripts."""
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    # ConfigParser interpolation treats "%" specially (URL-encoded passwords contain it).
    config.set_main_option(
        "sqlalchemy.url", settings.database_url.get_secret_value().replace("%", "%%")
    )
    return config


def upgrade(settings: Settings, revision: str = "head") -> None:
    """Apply migrations up to ``revision``."""
    command.upgrade(alembic_config(settings), revision)


def downgrade(settings: Settings, revision: str) -> None:
    """Roll migrations back to ``revision``."""
    command.downgrade(alembic_config(settings), revision)
