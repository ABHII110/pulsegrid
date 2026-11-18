"""Composition root: the only module that wires concrete adapters into services."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from pulsegrid.core.clock import Clock, SystemClock
from pulsegrid.core.config import Settings
from pulsegrid.db.session import create_engine, create_session_factory
from pulsegrid.domain.ports import DistributedLock, Notifier, UnitOfWork
from pulsegrid.integrations.locks import InMemoryLock, RedisLock
from pulsegrid.integrations.notifiers import LoggingNotifier, WebhookNotifier
from pulsegrid.integrations.prober import HttpxProber
from pulsegrid.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from pulsegrid.services.check_service import CheckService
from pulsegrid.services.monitor_service import MonitorService
from pulsegrid.services.reporting_service import ReportingService
from pulsegrid.workers.scheduler import Scheduler


@dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    """Fully wired application objects plus the resources they own."""

    settings: Settings
    engine: AsyncEngine
    http_client: httpx.AsyncClient
    redis: Redis | None
    monitor_service: MonitorService
    check_service: CheckService
    reporting_service: ReportingService
    scheduler: Scheduler

    async def aclose(self) -> None:
        """Release network clients and database connections."""
        await self.http_client.aclose()
        if self.redis is not None:
            await self.redis.aclose()
        await self.engine.dispose()


def build_container(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock | None = None,
) -> Container:
    """Wire the application from ``settings``.

    Args:
        settings: Runtime configuration.
        transport: Optional httpx transport override (used by tests to fake the network).
        clock: Optional time source override.
    """
    clock = clock or SystemClock()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    http_client = httpx.AsyncClient(
        transport=transport,
        headers={"User-Agent": settings.probe_user_agent},
        follow_redirects=False,
    )

    redis = (
        Redis.from_url(settings.redis_url.get_secret_value())
        if settings.redis_url is not None
        else None
    )
    lock: DistributedLock = RedisLock(redis) if redis is not None else InMemoryLock(clock)

    notifier: Notifier
    if settings.webhook_url is not None:
        notifier = WebhookNotifier(
            http_client,
            str(settings.webhook_url),
            secret=(
                settings.webhook_secret.get_secret_value()
                if settings.webhook_secret is not None
                else None
            ),
            timeout_seconds=settings.webhook_timeout_seconds,
        )
    else:
        notifier = LoggingNotifier()

    check_service = CheckService(uow_factory, HttpxProber(http_client), notifier, lock, clock)
    return Container(
        settings=settings,
        engine=engine,
        http_client=http_client,
        redis=redis,
        monitor_service=MonitorService(
            uow_factory, clock, allow_private_targets=settings.allow_private_targets
        ),
        check_service=check_service,
        reporting_service=ReportingService(uow_factory, clock),
        scheduler=Scheduler(
            uow_factory,
            check_service,
            clock,
            poll_interval_seconds=settings.worker_poll_interval_seconds,
            concurrency=settings.worker_concurrency,
            batch_size=settings.worker_batch_size,
        ),
    )


@asynccontextmanager
async def container_scope(settings: Settings) -> AsyncIterator[Container]:
    """Build a container and close its resources on exit."""
    container = build_container(settings)
    try:
        yield container
    finally:
        await container.aclose()
