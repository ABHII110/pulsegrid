from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import Connection, create_engine, inspect, select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pulsegrid.core.config import Settings
from pulsegrid.core.exceptions import RepositoryError
from pulsegrid.db import migrate
from pulsegrid.db.session import create_engine as create_async_db_engine
from pulsegrid.db.session import ping
from pulsegrid.db.tables import CheckResultRow, MonitorRow
from tests.factories import make_settings

pytestmark = pytest.mark.integration


def _monitor_row(**overrides: object) -> MonitorRow:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": uuid4(),
        "name": f"m-{uuid4().hex[:8]}",
        "url": "https://example.com/",
        "method": "GET",
        "interval_seconds": 60,
        "timeout_seconds": 5.0,
        "expected_status": 200,
        "failure_threshold": 3,
        "is_active": True,
        "status": "unknown",
        "consecutive_failures": 0,
        "last_checked_at": None,
        "next_check_at": now,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return MonitorRow(**values)


async def test_datetimes_round_trip_as_utc(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 1, 2, 9, 0, tzinfo=offset)
    row = _monitor_row(next_check_at=local)
    async with session_factory() as session:
        session.add(row)
        await session.commit()

    async with session_factory() as session:
        loaded = (await session.execute(select(MonitorRow))).scalar_one()

    assert loaded.next_check_at == local
    assert loaded.next_check_at.tzinfo == UTC
    assert loaded.last_checked_at is None


async def test_naive_datetimes_are_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(_monitor_row(next_check_at=datetime(2026, 1, 1)))
        with pytest.raises(StatementError, match="naive datetimes"):
            await session.commit()


async def test_deleting_monitor_cascades_on_sqlite(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    monitor = _monitor_row()
    async with session_factory() as session:
        session.add(monitor)
        await session.flush()
        session.add(
            CheckResultRow(
                id=uuid4(),
                monitor_id=monitor.id,
                checked_at=datetime.now(UTC),
                success=True,
                status_code=200,
                latency_ms=12.5,
                error=None,
            )
        )
        await session.commit()
        await session.delete(monitor)
        await session.commit()
        remaining = (await session.execute(select(CheckResultRow))).scalars().all()

    assert remaining == []


async def test_ping_succeeds(engine: AsyncEngine) -> None:
    await ping(engine)


async def test_ping_wraps_driver_errors(tmp_path: Path) -> None:
    missing_dir = tmp_path / "does-not-exist" / "db.sqlite"
    settings = make_settings(
        tmp_path, database_url=SecretStr(f"sqlite+aiosqlite:///{missing_dir.as_posix()}")
    )
    engine = create_async_db_engine(settings)
    with pytest.raises(RepositoryError, match="unreachable"):
        await ping(engine)
    await engine.dispose()


def _sync_url(settings: Settings) -> str:
    return settings.database_url.get_secret_value().replace("+aiosqlite", "")


def _schema_diff(connection: Connection) -> list[object]:
    context = MigrationContext.configure(connection)
    return list(compare_metadata(context, MonitorRow.metadata))


def test_migrations_match_models_and_downgrade_cleanly(settings: Settings) -> None:
    migrate.upgrade(settings)
    sync_engine = create_engine(_sync_url(settings))
    with sync_engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) >= {
            "monitors",
            "check_results",
            "incidents",
        }
        assert _schema_diff(connection) == []

    migrate.downgrade(settings, "base")
    with sync_engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) <= {"alembic_version"}
    sync_engine.dispose()


def test_alembic_config_escapes_percent_signs(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, database_url=SecretStr("postgresql+asyncpg://u:p%40ss@db/pulsegrid")
    )

    config = migrate.alembic_config(settings)

    assert config.get_main_option("sqlalchemy.url") == "postgresql+asyncpg://u:p%40ss@db/pulsegrid"
    assert config.get_main_option("script_location") == migrate.SCRIPT_LOCATION
