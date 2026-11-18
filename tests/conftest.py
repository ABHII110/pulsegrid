import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from pulsegrid.core.config import Settings, get_settings
from pulsegrid.db.session import create_engine, create_session_factory
from pulsegrid.db.tables import MonitorRow
from tests.factories import make_settings


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep .env, PULSEGRID_* variables and logging configuration from leaking between tests."""
    monkeypatch.chdir(tmp_path)
    for name in [key for key in os.environ if key.startswith("PULSEGRID_")]:
        monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    structlog.reset_defaults()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(MonitorRow.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)
