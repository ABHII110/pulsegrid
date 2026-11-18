from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pulsegrid.core.exceptions import DuplicateMonitorError, RepositoryError
from pulsegrid.domain.models import CheckResult, Incident, MonitorStatus
from pulsegrid.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from tests.fakes import T0, make_monitor

pytestmark = pytest.mark.integration


@pytest.fixture
def uow(session_factory: async_sessionmaker[AsyncSession]) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


def _check(monitor_id: object, minutes: int, *, success: bool, latency: float) -> CheckResult:
    return CheckResult(
        id=uuid4(),
        monitor_id=monitor_id,  # type: ignore[arg-type]
        checked_at=T0 + timedelta(minutes=minutes),
        success=success,
        status_code=200 if success else 500,
        latency_ms=latency,
        error=None if success else "boom",
    )


async def test_monitor_crud_round_trip(uow: SqlAlchemyUnitOfWork) -> None:
    monitor = make_monitor(name="api")
    async with uow:
        await uow.monitors.add(monitor)
        await uow.commit()

    async with uow:
        loaded = await uow.monitors.get(monitor.id)
        assert loaded == monitor
        assert loaded is not None
        loaded.status = MonitorStatus.DOWN
        loaded.name = "api-renamed"
        await uow.monitors.update(loaded)
        await uow.commit()

    async with uow:
        reloaded = await uow.monitors.get(monitor.id)
        assert reloaded is not None
        assert (reloaded.name, reloaded.status) == ("api-renamed", MonitorStatus.DOWN)
        assert await uow.monitors.delete(monitor.id) is True
        assert await uow.monitors.delete(monitor.id) is False
        await uow.commit()

    async with uow:
        assert await uow.monitors.get(monitor.id) is None


async def test_uncommitted_changes_are_rolled_back(uow: SqlAlchemyUnitOfWork) -> None:
    monitor = make_monitor()
    async with uow:
        await uow.monitors.add(monitor)

    async with uow:
        assert await uow.monitors.get(monitor.id) is None


async def test_duplicate_names_raise_domain_error(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        await uow.monitors.add(make_monitor(name="dup"))
        await uow.commit()

    with pytest.raises(DuplicateMonitorError):
        async with uow:
            await uow.monitors.add(make_monitor(name="dup"))

    other = make_monitor(name="other")
    async with uow:
        await uow.monitors.add(other)
        await uow.commit()
    with pytest.raises(DuplicateMonitorError):
        async with uow:
            await uow.monitors.update(replace(other, name="dup"))


async def test_updating_missing_rows_fails(uow: SqlAlchemyUnitOfWork) -> None:
    with pytest.raises(RepositoryError):
        async with uow:
            await uow.monitors.update(make_monitor())
    with pytest.raises(RepositoryError):
        async with uow:
            await uow.incidents.update(
                Incident(id=uuid4(), monitor_id=uuid4(), opened_at=T0, resolved_at=None, cause="x")
            )


async def test_list_page_orders_by_name(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        for name in ["charlie", "alpha", "bravo"]:
            await uow.monitors.add(make_monitor(name=name))
        await uow.commit()

    async with uow:
        page = await uow.monitors.list_page(limit=2, offset=1)

    assert [m.name for m in page] == ["bravo", "charlie"]


async def test_list_due_filters_and_orders(uow: SqlAlchemyUnitOfWork) -> None:
    overdue = make_monitor(name="overdue", next_check_at=T0 - timedelta(minutes=5))
    due_now = make_monitor(name="now", next_check_at=T0)
    future = make_monitor(name="future", next_check_at=T0 + timedelta(seconds=1))
    paused = make_monitor(name="paused", is_active=False, next_check_at=T0 - timedelta(hours=1))
    async with uow:
        for monitor in (due_now, future, paused, overdue):
            await uow.monitors.add(monitor)
        await uow.commit()

    async with uow:
        due = await uow.monitors.list_due(T0, limit=10)
        limited = await uow.monitors.list_due(T0, limit=1)

    assert [m.name for m in due] == ["overdue", "now"]
    assert [m.name for m in limited] == ["overdue"]


async def test_check_results_summary_listing_and_pruning(uow: SqlAlchemyUnitOfWork) -> None:
    monitor = make_monitor()
    async with uow:
        await uow.monitors.add(monitor)
        await uow.checks.add(_check(monitor.id, 0, success=True, latency=10.0))
        await uow.checks.add(_check(monitor.id, 1, success=False, latency=20.0))
        await uow.checks.add(_check(monitor.id, 2, success=True, latency=30.0))
        await uow.checks.add(_check(monitor.id, 60, success=True, latency=99.0))
        await uow.commit()

    async with uow:
        summary = await uow.checks.summarize(monitor.id, since=T0, until=T0 + timedelta(minutes=2))
        empty = await uow.checks.summarize(
            monitor.id, since=T0 + timedelta(days=1), until=T0 + timedelta(days=2)
        )
        recent = await uow.checks.list_recent(monitor.id, limit=2)

    assert (summary.total, summary.successful, summary.avg_latency_ms) == (3, 2, 20.0)
    assert (empty.total, empty.successful, empty.avg_latency_ms) == (0, 0, None)
    assert [r.latency_ms for r in recent] == [99.0, 30.0]

    async with uow:
        deleted = await uow.checks.delete_older_than(T0 + timedelta(minutes=2))
        await uow.commit()
    async with uow:
        remaining = await uow.checks.list_recent(monitor.id, limit=10)

    assert deleted == 2
    assert len(remaining) == 2


async def test_incident_queries(uow: SqlAlchemyUnitOfWork) -> None:
    first, second = make_monitor(), make_monitor()
    old = Incident(
        id=uuid4(),
        monitor_id=first.id,
        opened_at=T0,
        resolved_at=T0 + timedelta(minutes=1),
        cause="old",
    )
    current = Incident(
        id=uuid4(),
        monitor_id=first.id,
        opened_at=T0 + timedelta(hours=1),
        resolved_at=None,
        cause="now",
    )
    other = Incident(
        id=uuid4(),
        monitor_id=second.id,
        opened_at=T0 + timedelta(hours=2),
        resolved_at=None,
        cause="o",
    )
    async with uow:
        await uow.monitors.add(first)
        await uow.monitors.add(second)
        for incident in (old, current, other):
            await uow.incidents.add(incident)
        await uow.commit()

    async with uow:
        open_for_first = await uow.incidents.get_open_for_monitor(first.id)
        assert open_for_first == current
        everything = await uow.incidents.list_recent(monitor_id=None, open_only=False, limit=10)
        open_only = await uow.incidents.list_recent(monitor_id=None, open_only=True, limit=10)
        first_only = await uow.incidents.list_recent(monitor_id=first.id, open_only=False, limit=10)
        open_for_first.resolve(T0 + timedelta(hours=3))
        await uow.incidents.update(open_for_first)
        await uow.commit()

    assert [i.cause for i in everything] == ["o", "now", "old"]
    assert [i.cause for i in open_only] == ["o", "now"]
    assert [i.cause for i in first_only] == ["now", "old"]
    async with uow:
        assert await uow.incidents.get_open_for_monitor(first.id) is None


async def _query_missing_table(uow: SqlAlchemyUnitOfWork) -> None:
    async with uow:
        session = uow.monitors._session  # type: ignore[attr-defined]
        await session.execute(text("SELECT * FROM missing_table"))


async def test_driver_errors_become_repository_errors(uow: SqlAlchemyUnitOfWork) -> None:
    with pytest.raises(RepositoryError, match="database operation failed"):
        await _query_missing_table(uow)


async def test_repositories_unavailable_outside_context(uow: SqlAlchemyUnitOfWork) -> None:
    with pytest.raises(RepositoryError, match="outside"):
        _ = uow.monitors
    with pytest.raises(RepositoryError, match="outside"):
        _ = uow.checks
    with pytest.raises(RepositoryError, match="outside"):
        _ = uow.incidents
    with pytest.raises(RepositoryError, match="outside"):
        await uow.commit()
