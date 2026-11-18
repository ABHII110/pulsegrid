"""Read-side reporting: uptime, history, incidents and retention."""

from datetime import timedelta
from uuid import UUID

from pulsegrid.core.clock import Clock
from pulsegrid.core.exceptions import MonitorNotFoundError, ValidationFailedError
from pulsegrid.core.logging import get_logger
from pulsegrid.domain.models import CheckResult, Incident, UptimeReport
from pulsegrid.domain.ports import UnitOfWork, UnitOfWorkFactory

logger = get_logger(__name__)


class ReportingService:
    """Answers questions about monitor history.

    Args:
        uow_factory: Creates a unit of work per operation.
        clock: Time source.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def uptime(self, monitor_id: UUID, *, window: timedelta) -> UptimeReport:
        """Compute uptime for the trailing ``window``.

        Raises:
            ValidationFailedError: If the window is not positive.
            MonitorNotFoundError: If the monitor does not exist.
        """
        if window <= timedelta(0):
            msg = "window must be positive"
            raise ValidationFailedError(msg)
        until = self._clock.now()
        since = until - window
        async with self._uow_factory() as uow:
            await _require_monitor(uow, monitor_id)
            summary = await uow.checks.summarize(monitor_id, since=since, until=until)
        return UptimeReport(
            monitor_id=monitor_id,
            window_start=since,
            window_end=until,
            total_checks=summary.total,
            successful_checks=summary.successful,
            avg_latency_ms=summary.avg_latency_ms,
        )

    async def recent_checks(self, monitor_id: UUID, *, limit: int) -> list[CheckResult]:
        """Return the newest check results for a monitor.

        Raises:
            MonitorNotFoundError: If the monitor does not exist.
        """
        async with self._uow_factory() as uow:
            await _require_monitor(uow, monitor_id)
            return await uow.checks.list_recent(monitor_id, limit=limit)

    async def incidents(
        self, *, monitor_id: UUID | None = None, open_only: bool = False, limit: int = 50
    ) -> list[Incident]:
        """Return incidents newest first.

        Raises:
            MonitorNotFoundError: If ``monitor_id`` is given but does not exist.
        """
        async with self._uow_factory() as uow:
            if monitor_id is not None:
                await _require_monitor(uow, monitor_id)
            return await uow.incidents.list_recent(
                monitor_id=monitor_id, open_only=open_only, limit=limit
            )

    async def prune_checks(self, *, older_than: timedelta) -> int:
        """Delete check results older than ``older_than``; return how many were removed.

        Raises:
            ValidationFailedError: If ``older_than`` is shorter than one day.
        """
        if older_than < timedelta(days=1):
            msg = "retention must be at least one day"
            raise ValidationFailedError(msg)
        cutoff = self._clock.now() - older_than
        async with self._uow_factory() as uow:
            deleted = await uow.checks.delete_older_than(cutoff)
            await uow.commit()
        logger.info("checks_pruned", deleted=deleted, cutoff=cutoff.isoformat())
        return deleted


async def _require_monitor(uow: UnitOfWork, monitor_id: UUID) -> None:
    if await uow.monitors.get(monitor_id) is None:
        raise MonitorNotFoundError(monitor_id)
