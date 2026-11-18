import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pulsegrid.core.exceptions import RepositoryError
from pulsegrid.domain.models import Monitor, ProbeOutcome
from pulsegrid.domain.ports import UnitOfWork
from pulsegrid.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from pulsegrid.services.check_service import CheckService
from pulsegrid.workers.scheduler import Scheduler
from tests.fakes import OK, FakeClock, FakeLock, RecordingNotifier, ScriptedProber, make_monitor

pytestmark = pytest.mark.integration


class ConcurrencyProbe(ScriptedProber):
    def __init__(self) -> None:
        super().__init__([OK])
        self.active = 0
        self.peak = 0

    async def probe(self, monitor: Monitor) -> ProbeOutcome:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return await super().probe(monitor)


def _build(
    session_factory: async_sessionmaker[AsyncSession],
    prober: ScriptedProber,
    *,
    concurrency: int = 10,
    lock: FakeLock | None = None,
) -> tuple[Scheduler, FakeClock]:
    clock = FakeClock()

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    checks = CheckService(uow_factory, prober, RecordingNotifier(), lock or FakeLock(), clock)
    scheduler = Scheduler(
        uow_factory,
        checks,
        clock,
        poll_interval_seconds=0.01,
        concurrency=concurrency,
        batch_size=100,
    )
    return scheduler, clock


async def _add(session_factory: async_sessionmaker[AsyncSession], *monitors: Monitor) -> None:
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        for monitor in monitors:
            await uow.monitors.add(monitor)
        await uow.commit()


async def test_run_once_checks_only_due_monitors(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    prober = ScriptedProber()
    scheduler, clock = _build(session_factory, prober)
    due = make_monitor(name="due")
    later = make_monitor(name="later", next_check_at=clock.now() + timedelta(minutes=5))
    paused = make_monitor(name="paused", is_active=False)
    await _add(session_factory, due, later, paused)

    assert await scheduler.run_once() == 1
    assert [m.name for m in prober.probed] == ["due"]
    assert await scheduler.run_once() == 0

    clock.advance(minutes=5)
    assert await scheduler.run_once() == 2


async def test_concurrency_is_bounded(session_factory: async_sessionmaker[AsyncSession]) -> None:
    prober = ConcurrencyProbe()
    scheduler, _ = _build(session_factory, prober, concurrency=3)
    await _add(session_factory, *(make_monitor() for _ in range(10)))

    assert await scheduler.run_once() == 10
    assert prober.peak == 3


async def test_locked_monitors_are_not_counted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    lock = FakeLock()
    monitor = make_monitor()
    lock.held.add(f"pulsegrid:monitor:{monitor.id}")
    scheduler, _ = _build(session_factory, ScriptedProber(), lock=lock)
    await _add(session_factory, monitor)

    assert await scheduler.run_once() == 0


async def test_check_errors_are_contained(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class FailingLock(FakeLock):
        async def acquire(self, key: str, ttl_seconds: int) -> bool:
            msg = "redis down"
            raise RepositoryError(msg)

    scheduler, _ = _build(session_factory, ScriptedProber(), lock=FailingLock())
    await _add(session_factory, make_monitor())

    assert await scheduler.run_once() == 0


async def test_run_forever_survives_failures_and_stops(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scheduler, _ = _build(session_factory, ScriptedProber())
    passes = 0

    async def flaky_run_once() -> int:
        nonlocal passes
        passes += 1
        if passes == 1:
            msg = "transient"
            raise RuntimeError(msg)
        if passes >= 3:
            stop.set()
        return 0

    stop = asyncio.Event()
    scheduler.run_once = flaky_run_once  # type: ignore[method-assign]

    await asyncio.wait_for(scheduler.run_forever(stop), timeout=2)

    assert passes == 3
