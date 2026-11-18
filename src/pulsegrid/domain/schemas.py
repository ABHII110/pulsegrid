"""Pydantic DTOs: validated input for services and serialisable views for interfaces."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, UrlConstraints, model_validator

from pulsegrid.domain.models import HttpMethod, MonitorStatus

MonitorUrl = Annotated[
    AnyUrl,
    UrlConstraints(max_length=2048, allowed_schemes=["http", "https"], host_required=True),
]
MonitorName = Annotated[str, Field(min_length=1, max_length=100)]
IntervalSeconds = Annotated[int, Field(ge=10, le=86_400)]
TimeoutSeconds = Annotated[float, Field(ge=0.5, le=60)]
ExpectedStatus = Annotated[int, Field(ge=100, le=599)]
FailureThreshold = Annotated[int, Field(ge=1, le=20)]


class MonitorCreate(BaseModel):
    """Input for registering a monitor."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: MonitorName
    url: MonitorUrl
    method: HttpMethod = HttpMethod.GET
    interval_seconds: IntervalSeconds = 60
    timeout_seconds: TimeoutSeconds = 10.0
    expected_status: ExpectedStatus = 200
    failure_threshold: FailureThreshold = 3

    @model_validator(mode="after")
    def _timeout_shorter_than_interval(self) -> Self:
        if self.timeout_seconds >= self.interval_seconds:
            msg = "timeout_seconds must be shorter than interval_seconds"
            raise ValueError(msg)
        return self


class MonitorUpdate(BaseModel):
    """Partial update for a monitor. Omitted or null fields stay unchanged."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: MonitorName | None = None
    url: MonitorUrl | None = None
    method: HttpMethod | None = None
    interval_seconds: IntervalSeconds | None = None
    timeout_seconds: TimeoutSeconds | None = None
    expected_status: ExpectedStatus | None = None
    failure_threshold: FailureThreshold | None = None
    is_active: bool | None = None


class MonitorRead(BaseModel):
    """Public view of a monitor."""

    model_config = ConfigDict(from_attributes=True)

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


class CheckResultRead(BaseModel):
    """Public view of a check result."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    monitor_id: UUID
    checked_at: datetime
    success: bool
    status_code: int | None
    latency_ms: float
    error: str | None


class IncidentRead(BaseModel):
    """Public view of an incident."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    monitor_id: UUID
    opened_at: datetime
    resolved_at: datetime | None
    cause: str
    is_open: bool


class UptimeReportRead(BaseModel):
    """Public view of an uptime report."""

    model_config = ConfigDict(from_attributes=True)

    monitor_id: UUID
    window_start: datetime
    window_end: datetime
    total_checks: int
    successful_checks: int
    uptime_percent: float | None
    avg_latency_ms: float | None
