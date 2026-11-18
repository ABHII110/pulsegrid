"""In-memory test doubles for the domain ports."""

from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pulsegrid.core.exceptions import NotificationError
from pulsegrid.domain.models import (
    HttpMethod,
    IncidentEvent,
    Monitor,
    MonitorStatus,
    ProbeOutcome,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

OK = ProbeOutcome(success=True, status_code=200, latency_ms=42.0, error=None)
FAIL = ProbeOutcome(success=False, status_code=503, latency_ms=17.0, error="expected 200, got 503")


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


class ScriptedProber:
    """Returns queued outcomes in order, then repeats the last one."""

    def __init__(self, outcomes: Iterable[ProbeOutcome] = (OK,)) -> None:
        self._outcomes = deque(outcomes)
        self.probed: list[Monitor] = []

    def queue(self, *outcomes: ProbeOutcome) -> None:
        self._outcomes.extend(outcomes)

    async def probe(self, monitor: Monitor) -> ProbeOutcome:
        self.probed.append(monitor)
        if len(self._outcomes) > 1:
            return self._outcomes.popleft()
        return self._outcomes[0]


class RecordingNotifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[IncidentEvent] = []
        self.fail = fail

    async def notify(self, event: IncidentEvent) -> None:
        if self.fail:
            msg = "webhook down"
            raise NotificationError(msg)
        self.events.append(event)


class FakeLock:
    def __init__(self) -> None:
        self.held: set[str] = set()
        self.acquired: list[tuple[str, int]] = []

    async def acquire(self, key: str, ttl_seconds: int) -> bool:
        if key in self.held:
            return False
        self.held.add(key)
        self.acquired.append((key, ttl_seconds))
        return True

    async def release(self, key: str) -> None:
        self.held.discard(key)


def make_monitor(**overrides: object) -> Monitor:
    values: dict[str, object] = {
        "id": uuid4(),
        "name": f"monitor-{uuid4().hex[:8]}",
        "url": "https://example.com/health",
        "method": HttpMethod.GET,
        "interval_seconds": 60,
        "timeout_seconds": 5.0,
        "expected_status": 200,
        "failure_threshold": 3,
        "is_active": True,
        "status": MonitorStatus.UNKNOWN,
        "consecutive_failures": 0,
        "last_checked_at": None,
        "next_check_at": T0,
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return Monitor(**values)  # type: ignore[arg-type]
