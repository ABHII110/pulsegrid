from datetime import timedelta
from uuid import uuid4

import pytest

from pulsegrid.domain.models import Incident, MonitorStatus, StatusTransition, UptimeReport
from tests.fakes import FAIL, OK, T0, make_monitor


def test_new_monitor_is_due_immediately() -> None:
    monitor = make_monitor()

    assert monitor.is_due(T0)
    assert not monitor.is_due(T0 - timedelta(seconds=1))


def test_inactive_monitor_is_never_due() -> None:
    assert not make_monitor(is_active=False).is_due(T0 + timedelta(days=1))


def test_success_marks_up_and_schedules_next_check() -> None:
    monitor = make_monitor(consecutive_failures=2)
    at = T0 + timedelta(seconds=3)

    transition = monitor.record_outcome(OK, at)

    assert transition is StatusTransition.NONE
    assert monitor.status is MonitorStatus.UP
    assert monitor.consecutive_failures == 0
    assert monitor.last_checked_at == at
    assert monitor.updated_at == at
    assert monitor.next_check_at == at + timedelta(seconds=60)


def test_goes_down_only_when_threshold_reached() -> None:
    monitor = make_monitor(status=MonitorStatus.UP, failure_threshold=3)

    transitions = [monitor.record_outcome(FAIL, T0) for _ in range(3)]

    assert transitions == [StatusTransition.NONE, StatusTransition.NONE, StatusTransition.WENT_DOWN]
    assert monitor.status is MonitorStatus.DOWN
    assert monitor.consecutive_failures == 3


def test_further_failures_while_down_do_not_reopen() -> None:
    monitor = make_monitor(failure_threshold=1)
    monitor.record_outcome(FAIL, T0)

    assert monitor.record_outcome(FAIL, T0) is StatusTransition.NONE
    assert monitor.consecutive_failures == 2


def test_recovery_after_down() -> None:
    monitor = make_monitor(failure_threshold=1)
    monitor.record_outcome(FAIL, T0)

    assert monitor.record_outcome(OK, T0) is StatusTransition.RECOVERED
    assert monitor.status is MonitorStatus.UP


def test_failure_below_threshold_keeps_unknown_status() -> None:
    monitor = make_monitor(failure_threshold=2)
    monitor.record_outcome(FAIL, T0)

    assert monitor.status is MonitorStatus.UNKNOWN


def test_incident_resolve_is_idempotent() -> None:
    incident = Incident(id=uuid4(), monitor_id=uuid4(), opened_at=T0, resolved_at=None, cause="x")
    was_open = incident.is_open

    incident.resolve(T0 + timedelta(minutes=5))
    incident.resolve(T0 + timedelta(minutes=9))

    assert was_open
    assert not incident.is_open
    assert incident.resolved_at == T0 + timedelta(minutes=5)


@pytest.mark.parametrize(
    ("total", "successful", "expected"),
    [(0, 0, None), (4, 3, 75.0), (3, 2, 66.667), (10, 10, 100.0)],
)
def test_uptime_percent(total: int, successful: int, expected: float | None) -> None:
    report = UptimeReport(
        monitor_id=uuid4(),
        window_start=T0,
        window_end=T0,
        total_checks=total,
        successful_checks=successful,
        avg_latency_ms=None,
    )

    assert report.uptime_percent == expected
