"""Async engine and session factory construction."""

from typing import Any

from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pulsegrid.core.config import Settings
from pulsegrid.core.exceptions import RepositoryError


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine described by ``settings``.

    SQLite connections get foreign-key enforcement switched on so ``ON DELETE CASCADE`` behaves
    the same as on PostgreSQL.
    """
    url = settings.database_url.get_secret_value()
    if settings.is_sqlite:
        engine = create_async_engine(url, echo=settings.db_echo)

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_async_engine(url, echo=settings.db_echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory whose objects stay usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
    """Verify the database answers a trivial query.

    Raises:
        RepositoryError: If the database cannot be reached.
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        msg = "database is unreachable"
        raise RepositoryError(msg) from exc
