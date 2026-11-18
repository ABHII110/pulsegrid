from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pulsegrid.core.exceptions import (
    CheckInProgressError,
    DuplicateMonitorError,
    MonitorNotFoundError,
    ValidationFailedError,
)
from pulsegrid.domain.models import IncidentEventType, Monitor, MonitorStatus, ProbeOutcome
from pulsegrid.domain.schemas import MonitorCreate, MonitorUpdate
from pulsegrid.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from pulsegrid.services.check_service import CheckService, lock_key
from pulsegrid.services.monitor_service import MonitorService, ensure_public_target
from pulsegrid.services.reporting_service import ReportingService
from tests.fakes import FAIL, OK, T0, FakeClock, FakeLock, RecordingNotifier, ScriptedProber

pytestmark = pytest.mark.integration


@dataclass
class Harness:
    clock: FakeClock
    prober: ScriptedProber
    notifier: RecordingNotifier
    lock: FakeLock
    monitors: MonitorService
    checks: CheckService
    reports: ReportingService


@pytest.fixture
def harness(session_factory: async_sessionmaker[AsyncSession]) -> Harness:
    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    clock, prober, notifier, lock = FakeClock(), ScriptedProber(), RecordingNotifier(), FakeLock()
    return Harness(
        clock=clock,
        prober=prober,
        notifier=notifier,
        lock=lock,
        monitors=MonitorService(uow_factory, clock, allow_private_targets=False),
        checks=CheckService(uow_factory, prober, notifier, lock, clock),
        reports=ReportingService(uow_factory, clock),
    )


def _create(name: str = "api", **extra: object) -> MonitorCreate:
    return MonitorCreate.model_validate({"name": name, "url": "https://example.com/", **extra})


# --- MonitorService ---------------------------------------------------------------------------


async def test_create_and_get(harness: Harness) -> None:
    created = await harness.monitors.create(_create(failure_threshold=2))

    fetched = await harness.monitors.get(created.id)

    assert fetched == created
    assert fetched.status is MonitorStatus.UNKNOWN
    assert fetched.next_check_at == T0
    assert fetched.failure_threshold == 2


async def test_create_rejects_duplicates(harness: Harness) -> None:
    await harness.monitors.create(_create("same"))
    with pytest.raises(DuplicateMonitorError):
        await harness.monitors.create(_create("same"))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/",
        "http://api.localhost/",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://192.168.0.10/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
def test_private_targets_are_rejected(url: str) -> None:
    with pytest.raises(ValidationFailedError):
        ensure_public_target(url)


@pytest.mark.parametrize(
    "url", ["https://example.com/", "http://8.8.8.8/", "http://[2606:4700::1]/"]
)
def test_public_targets_are_accepted(url: str) -> None:
    ensure_public_target(url)


async def test_private_target_allowed_when_configured(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = MonitorService(
        lambda: SqlAlchemyUnitOfWork(session_factory), FakeClock(), allow_private_targets=True
    )
    created = await service.create(
        MonitorCreate.model_validate({"name": "local", "url": "http://127.0.0.1:9000/"})
    )
    assert created.url == "http://127.0.0.1:9000/"


async def test_create_rejects_private_target(harness: Harness) -> None:
    with pytest.raises(ValidationFailedError):
        await harness.monitors.create(
            MonitorCreate.model_validate({"name": "x", "url": "http://127.0.0.1/"})
        )


async def test_list_monitors(harness: Harness) -> None:
    await harness.monitors.create(_create("b"))
    await harness.monitors.create(_create("a"))

    names = [m.name for m in await harness.monitors.list_monitors(limit=10, offset=0)]

    assert names == ["a", "b"]


async def test_update_applies_changes(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    harness.clock.advance(minutes=1)

    updated = await harness.monitors.update(
        created.id,
        MonitorUpdate.model_validate(
            {
                "name": "renamed",
                "url": "https://example.org/status",
                "method": "HEAD",
                "timeout_seconds": 2,
                "expected_status": 204,
                "failure_threshold": 5,
                "interval_seconds": 30,
            }
        ),
    )

    assert updated.name == "renamed"
    assert updated.url == "https://example.org/status"
    assert updated.method.value == "HEAD"
    assert (updated.timeout_seconds, updated.expected_status) == (2, 204)
    assert (updated.failure_threshold, updated.interval_seconds) == (5, 30)
    assert updated.updated_at == T0 + timedelta(minutes=1)
    assert await harness.monitors.get(created.id) == updated


async def test_shorter_interval_pulls_next_check_forward(harness: Harness) -> None:
    created = await harness.monitors.create(_create(interval_seconds=3600))
    await harness.checks.run_check(created.id)

    updated = await harness.monitors.update(
        created.id, MonitorUpdate.model_validate({"interval_seconds": 60})
    )

    assert updated.next_check_at == T0 + timedelta(seconds=60)


async def test_pause_and_resume(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    await harness.checks.run_check(created.id)

    paused = await harness.monitors.update(created.id, MonitorUpdate(is_active=False))
    assert not paused.is_active

    harness.clock.advance(minutes=10)
    resumed = await harness.monitors.update(created.id, MonitorUpdate(is_active=True))
    assert resumed.is_active
    assert resumed.next_check_at == harness.clock.now()


async def test_update_rejects_timeout_not_below_interval(harness: Harness) -> None:
    created = await harness.monitors.create(_create(interval_seconds=10, timeout_seconds=5))
    with pytest.raises(ValidationFailedError):
        await harness.monitors.update(created.id, MonitorUpdate(timeout_seconds=10))


async def test_update_rejects_private_url(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    with pytest.raises(ValidationFailedError):
        await harness.monitors.update(
            created.id, MonitorUpdate.model_validate({"url": "http://10.0.0.1/"})
        )


async def test_missing_monitor_errors(harness: Harness) -> None:
    missing = uuid4()
    with pytest.raises(MonitorNotFoundError):
        await harness.monitors.get(missing)
    with pytest.raises(MonitorNotFoundError):
        await harness.monitors.update(missing, MonitorUpdate(name="x"))
    with pytest.raises(MonitorNotFoundError):
        await harness.monitors.delete(missing)
    with pytest.raises(MonitorNotFoundError):
        await harness.checks.run_check(missing)


async def test_delete(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    await harness.monitors.delete(created.id)
    with pytest.raises(MonitorNotFoundError):
        await harness.monitors.get(created.id)


# --- CheckService -----------------------------------------------------------------------------


async def test_successful_check_is_recorded(harness: Harness) -> None:
    created = await harness.monitors.create(_create())

    result = await harness.checks.run_check(created.id)

    assert result.success
    assert result.checked_at == T0
    monitor = await harness.monitors.get(created.id)
    assert monitor.status is MonitorStatus.UP
    assert monitor.next_check_at == T0 + timedelta(seconds=60)
    assert harness.lock.held == set()
    assert harness.lock.acquired == [(lock_key(created.id), 10 + 30)]
    assert harness.notifier.events == []


async def test_incident_lifecycle_and_notifications(harness: Harness) -> None:
    created = await harness.monitors.create(_create(failure_threshold=2))
    harness.prober.queue(FAIL, FAIL, FAIL, OK)
    harness.prober._outcomes.popleft()  # drop the default OK so the script starts with FAIL

    for _ in range(3):
        await harness.checks.run_check(created.id)
    opened = await harness.reports.incidents(monitor_id=created.id, open_only=True)
    assert len(opened) == 1
    assert opened[0].cause == FAIL.error
    assert [e.type for e in harness.notifier.events] == [IncidentEventType.OPENED]

    harness.clock.advance(minutes=5)
    await harness.checks.run_check(created.id)

    incidents = await harness.reports.incidents(monitor_id=created.id)
    assert len(incidents) == 1
    assert incidents[0].resolved_at == T0 + timedelta(minutes=5)
    assert [e.type for e in harness.notifier.events] == [
        IncidentEventType.OPENED,
        IncidentEventType.RESOLVED,
    ]
    assert harness.notifier.events[1].monitor.status is MonitorStatus.UP


async def test_notification_failure_does_not_fail_check(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    monitors = MonitorService(uow_factory, clock, allow_private_targets=False)
    checks = CheckService(
        uow_factory, ScriptedProber([FAIL]), RecordingNotifier(fail=True), FakeLock(), clock
    )
    created = await monitors.create(_create(failure_threshold=1))

    result = await checks.run_check(created.id)

    assert not result.success
    assert (await monitors.get(created.id)).status is MonitorStatus.DOWN


async def test_locked_monitor_is_skipped(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    harness.lock.held.add(lock_key(created.id))

    assert await harness.checks.check_monitor(created, force=False) is None
    with pytest.raises(CheckInProgressError):
        await harness.checks.run_check(created.id)
    assert harness.prober.probed == []


async def test_stale_snapshot_is_skipped_when_not_forced(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    await harness.checks.run_check(created.id)

    assert await harness.checks.check_monitor(created, force=False) is None
    assert len(harness.prober.probed) == 1
    assert harness.lock.held == set()


async def test_monitor_deleted_before_scheduled_check_is_skipped(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    await harness.monitors.delete(created.id)

    assert await harness.checks.check_monitor(created, force=False) is None


async def test_monitor_deleted_during_probe_raises(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    monitors = harness.monitors

    class DeletingProber(ScriptedProber):
        async def probe(self, monitor: Monitor) -> ProbeOutcome:
            await monitors.delete(created.id)
            return OK

    service = CheckService(
        lambda: harness.monitors._uow_factory(),
        DeletingProber(),
        harness.notifier,
        harness.lock,
        harness.clock,
    )
    with pytest.raises(MonitorNotFoundError):
        await service.check_monitor(created, force=True)
    assert harness.lock.held == set()


# --- ReportingService -------------------------------------------------------------------------


async def test_uptime_and_recent_checks(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    harness.prober.queue(FAIL, OK)
    for _ in range(4):
        await harness.checks.run_check(created.id)
        harness.clock.advance(minutes=1)

    report = await harness.reports.uptime(created.id, window=timedelta(hours=1))
    recent = await harness.reports.recent_checks(created.id, limit=2)

    assert (report.total_checks, report.successful_checks) == (4, 3)
    assert report.uptime_percent == 75.0
    assert report.window_end == harness.clock.now()
    assert len(recent) == 2
    assert recent[0].checked_at > recent[1].checked_at


async def test_uptime_validation_and_missing_monitor(harness: Harness) -> None:
    with pytest.raises(ValidationFailedError):
        await harness.reports.uptime(uuid4(), window=timedelta(0))
    with pytest.raises(MonitorNotFoundError):
        await harness.reports.uptime(uuid4(), window=timedelta(hours=1))
    with pytest.raises(MonitorNotFoundError):
        await harness.reports.recent_checks(uuid4(), limit=5)
    with pytest.raises(MonitorNotFoundError):
        await harness.reports.incidents(monitor_id=uuid4())


async def test_prune_checks(harness: Harness) -> None:
    created = await harness.monitors.create(_create())
    await harness.checks.run_check(created.id)
    harness.clock.advance(days=40)
    await harness.checks.run_check(created.id)

    deleted = await harness.reports.prune_checks(older_than=timedelta(days=30))

    assert deleted == 1
    assert len(await harness.reports.recent_checks(created.id, limit=10)) == 1
    with pytest.raises(ValidationFailedError):
        await harness.reports.prune_checks(older_than=timedelta(hours=1))
