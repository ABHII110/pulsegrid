"""Domain entities and the monitor health state machine."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID


class MonitorStatus(StrEnum):
    """Health of a monitor as seen by its most recent checks."""

    UNKNOWN = "unknown"
    UP = "up"
    DOWN = "down"


class HttpMethod(StrEnum):
    """HTTP methods a probe may use. Both are safe and idempotent."""

    GET = "GET"
    HEAD = "HEAD"


class StatusTransition(StrEnum):
    """Health change caused by recording a probe outcome."""

    NONE = "none"
    WENT_DOWN = "went_down"
    RECOVERED = "recovered"


class IncidentEventType(StrEnum):
    """Kinds of incident notifications."""

    OPENED = "incident.opened"
    RESOLVED = "incident.resolved"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProbeOutcome:
    """Raw result of probing an endpoint once.

    Attributes:
        success: Whether the response matched the monitor's expectations.
        status_code: HTTP status received, or ``None`` if no response arrived.
        latency_ms: Time until response headers arrived (or until failure).
        error: Failure description; ``None`` on success.
    """

    success: bool
    status_code: int | None
    latency_ms: float
    error: str | None


@dataclass(slots=True, kw_only=True)
class Monitor:
    """An HTTP endpoint under observation and its current health."""

    id: UUID
    name: str
    url: str
    method: HttpMethod
    interval_seconds: int
    timeout_seconds: float
    expected_status: int
    failure_threshold: int
    is_active: bool
    status: MonitorStatus
    consecutive_failures: int
    last_checked_at: datetime | None
    next_check_at: datetime
    created_at: datetime
    updated_at: datetime

    def is_due(self, now: datetime) -> bool:
        """Whether the monitor should be probed at ``now``."""
        return self.is_active and self.next_check_at <= now

    def record_outcome(self, outcome: ProbeOutcome, at: datetime) -> StatusTransition:
        """Apply a probe outcome to the health state.

        Args:
            outcome: Result of the probe.
            at: When the probe completed.

        Returns:
            The health transition this outcome caused, if any.
        """
        self.last_checked_at = at
        self.next_check_at = at + timedelta(seconds=self.interval_seconds)
        self.updated_at = at

        if outcome.success:
            was_down = self.status is MonitorStatus.DOWN
            self.consecutive_failures = 0
            self.status = MonitorStatus.UP
            return StatusTransition.RECOVERED if was_down else StatusTransition.NONE

        self.consecutive_failures += 1
        if (
            self.consecutive_failures >= self.failure_threshold
            and self.status is not MonitorStatus.DOWN
        ):
            self.status = MonitorStatus.DOWN
            return StatusTransition.WENT_DOWN
        return StatusTransition.NONE


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckResult:
    """A persisted probe outcome."""

    id: UUID
    monitor_id: UUID
    checked_at: datetime
    success: bool
    status_code: int | None
    latency_ms: float
    error: str | None


@dataclass(slots=True, kw_only=True)
class Incident:
    """A period during which a monitor was down."""

    id: UUID
    monitor_id: UUID
    opened_at: datetime
    resolved_at: datetime | None
    cause: str

    @property
    def is_open(self) -> bool:
        """Whether the incident is still ongoing."""
        return self.resolved_at is None

    def resolve(self, at: datetime) -> None:
        """Close the incident at ``at``; resolving twice keeps the first time."""
        if self.resolved_at is None:
            self.resolved_at = at


@dataclass(frozen=True, slots=True, kw_only=True)
class IncidentEvent:
    """Notification payload emitted when an incident opens or resolves."""

    type: IncidentEventType
    incident: Incident
    monitor: Monitor


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckSummary:
    """Aggregate of check results over a time window."""

    total: int
    successful: int
    avg_latency_ms: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class UptimeReport:
    """Uptime statistics for one monitor over a window."""

    monitor_id: UUID
    window_start: datetime
    window_end: datetime
    total_checks: int
    successful_checks: int
    avg_latency_ms: float | None

    @property
    def uptime_percent(self) -> float | None:
        """Share of successful checks in percent, or ``None`` without data."""
        if self.total_checks == 0:
            return None
        return round(100.0 * self.successful_checks / self.total_checks, 3)
