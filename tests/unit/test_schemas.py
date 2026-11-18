from typing import Any

import pytest
from pydantic import ValidationError

from pulsegrid.domain.models import HttpMethod, UptimeReport
from pulsegrid.domain.schemas import MonitorCreate, MonitorRead, MonitorUpdate, UptimeReportRead
from tests.fakes import T0, make_monitor


def test_create_applies_defaults_and_strips_whitespace() -> None:
    data = MonitorCreate.model_validate({"name": "  api  ", "url": "https://example.com"})

    assert data.name == "api"
    assert str(data.url) == "https://example.com/"
    assert data.method is HttpMethod.GET
    assert (data.interval_seconds, data.timeout_seconds) == (60, 10.0)
    assert (data.expected_status, data.failure_threshold) == (200, 3)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "url": "https://example.com"},
        {"name": "x" * 101, "url": "https://example.com"},
        {"name": "a", "url": "ftp://example.com"},
        {"name": "a", "url": "not a url"},
        {"name": "a", "url": "https://example.com/" + "p" * 2100},
        {"name": "a", "url": "https://example.com", "method": "POST"},
        {"name": "a", "url": "https://example.com", "interval_seconds": 5},
        {"name": "a", "url": "https://example.com", "timeout_seconds": 0.1},
        {"name": "a", "url": "https://example.com", "expected_status": 600},
        {"name": "a", "url": "https://example.com", "failure_threshold": 0},
        {"name": "a", "url": "https://example.com", "unexpected": True},
        {"name": "a", "url": "https://example.com", "interval_seconds": 10, "timeout_seconds": 10},
    ],
)
def test_create_rejects_invalid_input(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MonitorCreate.model_validate(payload)


def test_update_tracks_only_provided_fields() -> None:
    update = MonitorUpdate.model_validate({"is_active": False})

    assert update.model_dump(exclude_unset=True) == {"is_active": False}


def test_update_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MonitorUpdate.model_validate({"status": "up"})


def test_read_models_load_from_domain_objects() -> None:
    monitor = make_monitor()
    read = MonitorRead.model_validate(monitor)
    assert read.id == monitor.id
    assert read.status == monitor.status

    report = UptimeReport(
        monitor_id=monitor.id,
        window_start=T0,
        window_end=T0,
        total_checks=2,
        successful_checks=1,
        avg_latency_ms=10.0,
    )
    assert UptimeReportRead.model_validate(report).uptime_percent == 50.0
