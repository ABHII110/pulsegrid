"""Interfaces the services depend on. Adapters live in ``repositories`` and ``integrations``."""

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pulsegrid.domain.models import (
    CheckResult,
    CheckSummary,
    Incident,
    IncidentEvent,
    Monitor,
    ProbeOutcome,
)


class MonitorRepository(Protocol):
    """Persistence for monitors."""

    async def add(self, monitor: Monitor) -> None:
        """Insert a monitor; raises ``DuplicateMonitorError`` on a name clash."""

    async def get(self, monitor_id: UUID) -> Monitor | None:
        """Return the monitor or ``None``."""

    async def list_page(self, *, limit: int, offset: int) -> list[Monitor]:
        """Return monitors ordered by name."""

    async def list_due(self, now: datetime, *, limit: int) -> list[Monitor]:
        """Return active monitors whose next check is at or before ``now``, oldest first."""

    async def update(self, monitor: Monitor) -> None:
        """Persist changes; raises ``DuplicateMonitorError`` on a name clash."""

    async def delete(self, monitor_id: UUID) -> bool:
        """Delete the monitor and its history; return whether it existed."""


class CheckResultRepository(Protocol):
    """Persistence for check results."""

    async def add(self, result: CheckResult) -> None:
        """Insert a check result."""

    async def list_recent(self, monitor_id: UUID, *, limit: int) -> list[CheckResult]:
        """Return the newest results first."""

    async def summarize(
        self, monitor_id: UUID, *, since: datetime, until: datetime
    ) -> CheckSummary:
        """Aggregate results with ``since <= checked_at <= until``."""

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Delete results checked before ``cutoff``; return how many were removed."""


class IncidentRepository(Protocol):
    """Persistence for incidents."""

    async def add(self, incident: Incident) -> None:
        """Insert an incident."""

    async def get_open_for_monitor(self, monitor_id: UUID) -> Incident | None:
        """Return the monitor's unresolved incident, if any."""

    async def update(self, incident: Incident) -> None:
        """Persist changes to an incident."""

    async def list_recent(
        self, *, monitor_id: UUID | None, open_only: bool, limit: int
    ) -> list[Incident]:
        """Return incidents newest first, optionally filtered."""


class UnitOfWork(Protocol):
    """Transaction boundary grouping the repositories.

    Changes are discarded unless ``commit`` is awaited inside the ``async with`` block.
    """

    @property
    def monitors(self) -> MonitorRepository:
        """Monitor repository bound to this transaction."""

    @property
    def checks(self) -> CheckResultRepository:
        """Check result repository bound to this transaction."""

    @property
    def incidents(self) -> IncidentRepository:
        """Incident repository bound to this transaction."""

    async def commit(self) -> None:
        """Commit the transaction."""

    async def __aenter__(self) -> Self:
        """Open the transaction."""

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Roll back uncommitted work and release the connection."""


UnitOfWorkFactory = Callable[[], UnitOfWork]


class Prober(Protocol):
    """Performs a single probe of a monitor's endpoint."""

    async def probe(self, monitor: Monitor) -> ProbeOutcome:
        """Probe the endpoint. Must not raise for network or HTTP failures."""


class Notifier(Protocol):
    """Delivers incident notifications."""

    async def notify(self, event: IncidentEvent) -> None:
        """Deliver the event; raises ``NotificationError`` on failure."""


class DistributedLock(Protocol):
    """Mutual exclusion across worker processes."""

    async def acquire(self, key: str, ttl_seconds: int) -> bool:
        """Try to take ``key`` for ``ttl_seconds``; return whether it was acquired."""

    async def release(self, key: str) -> None:
        """Release ``key`` if this instance still holds it."""
