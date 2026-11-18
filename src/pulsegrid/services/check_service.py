"""Probing monitors, recording results and driving the incident lifecycle."""

from uuid import UUID, uuid4

from pulsegrid.core.clock import Clock
from pulsegrid.core.exceptions import (
    CheckInProgressError,
    MonitorNotFoundError,
    NotificationError,
)
from pulsegrid.core.logging import get_logger
from pulsegrid.domain.models import (
    CheckResult,
    Incident,
    IncidentEvent,
    IncidentEventType,
    Monitor,
    ProbeOutcome,
    StatusTransition,
)
from pulsegrid.domain.ports import DistributedLock, Notifier, Prober, UnitOfWorkFactory

logger = get_logger(__name__)

LOCK_GRACE_SECONDS = 30


def lock_key(monitor_id: UUID) -> str:
    """Name of the distributed lock guarding checks of one monitor."""
    return f"pulsegrid:monitor:{monitor_id}"


class CheckService:
    """Runs checks and keeps monitor health, history and incidents consistent.

    Args:
        uow_factory: Creates a unit of work per transaction.
        prober: Performs HTTP probes.
        notifier: Delivers incident events.
        lock: Prevents two processes from checking the same monitor at once.
        clock: Time source.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        prober: Prober,
        notifier: Notifier,
        lock: DistributedLock,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._prober = prober
        self._notifier = notifier
        self._lock = lock
        self._clock = clock

    async def run_check(self, monitor_id: UUID) -> CheckResult:
        """Check a monitor now, regardless of its schedule.

        Raises:
            MonitorNotFoundError: If the monitor does not exist.
            CheckInProgressError: If another process is checking it.
        """
        async with self._uow_factory() as uow:
            monitor = await uow.monitors.get(monitor_id)
        if monitor is None:
            raise MonitorNotFoundError(monitor_id)
        result = await self.check_monitor(monitor, force=True)
        if result is None:
            raise CheckInProgressError(monitor_id)
        return result

    async def check_monitor(self, monitor: Monitor, *, force: bool) -> CheckResult | None:
        """Probe ``monitor`` and record the outcome.

        Args:
            monitor: Monitor to check (may be a stale snapshot).
            force: Check even if the monitor is no longer due.

        Returns:
            The recorded result, or ``None`` when the check was skipped because another
            process holds the lock or (without ``force``) the monitor is no longer due.

        Raises:
            MonitorNotFoundError: If the monitor was deleted during the probe.
        """
        key = lock_key(monitor.id)
        if not await self._lock.acquire(key, int(monitor.timeout_seconds) + LOCK_GRACE_SECONDS):
            logger.debug("check_skipped_locked", monitor_id=str(monitor.id))
            return None
        try:
            if not force:
                async with self._uow_factory() as uow:
                    current = await uow.monitors.get(monitor.id)
                if current is None or not current.is_due(self._clock.now()):
                    return None
                monitor = current
            outcome = await self._prober.probe(monitor)
            result, events = await self._record(monitor.id, outcome)
        finally:
            await self._lock.release(key)

        logger.info(
            "check_recorded",
            monitor_id=str(result.monitor_id),
            success=result.success,
            status_code=result.status_code,
            latency_ms=result.latency_ms,
        )
        for event in events:
            await self._dispatch(event)
        return result

    async def _record(
        self, monitor_id: UUID, outcome: ProbeOutcome
    ) -> tuple[CheckResult, list[IncidentEvent]]:
        now = self._clock.now()
        events: list[IncidentEvent] = []
        async with self._uow_factory() as uow:
            monitor = await uow.monitors.get(monitor_id)
            if monitor is None:
                raise MonitorNotFoundError(monitor_id)

            transition = monitor.record_outcome(outcome, now)
            result = CheckResult(
                id=uuid4(),
                monitor_id=monitor_id,
                checked_at=now,
                success=outcome.success,
                status_code=outcome.status_code,
                latency_ms=outcome.latency_ms,
                error=outcome.error,
            )
            await uow.checks.add(result)

            if transition is StatusTransition.WENT_DOWN:
                incident = Incident(
                    id=uuid4(),
                    monitor_id=monitor_id,
                    opened_at=now,
                    resolved_at=None,
                    cause=(outcome.error or "probe failed")[:500],
                )
                await uow.incidents.add(incident)
                events.append(
                    IncidentEvent(type=IncidentEventType.OPENED, incident=incident, monitor=monitor)
                )
            elif transition is StatusTransition.RECOVERED:
                open_incident = await uow.incidents.get_open_for_monitor(monitor_id)
                if open_incident is not None:
                    open_incident.resolve(now)
                    await uow.incidents.update(open_incident)
                    events.append(
                        IncidentEvent(
                            type=IncidentEventType.RESOLVED, incident=open_incident, monitor=monitor
                        )
                    )

            await uow.monitors.update(monitor)
            await uow.commit()
        return result, events

    async def _dispatch(self, event: IncidentEvent) -> None:
        log = logger.bind(
            event_type=event.type.value,
            monitor_id=str(event.monitor.id),
            incident_id=str(event.incident.id),
        )
        try:
            await self._notifier.notify(event)
        except NotificationError as exc:
            log.warning("notification_failed", error=exc.message)
        else:
            log.info("notification_sent")
