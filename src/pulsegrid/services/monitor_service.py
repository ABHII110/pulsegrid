"""Monitor registration and lifecycle."""

import ipaddress
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pulsegrid.core.clock import Clock
from pulsegrid.core.exceptions import MonitorNotFoundError, ValidationFailedError
from pulsegrid.core.logging import get_logger
from pulsegrid.domain.models import HttpMethod, Monitor, MonitorStatus
from pulsegrid.domain.ports import UnitOfWorkFactory
from pulsegrid.domain.schemas import MonitorCreate, MonitorUpdate

logger = get_logger(__name__)


def ensure_public_target(url: str) -> None:
    """Reject URLs that point at the local machine or private networks.

    Only IP literals and ``localhost`` names are checked; hostnames are not resolved here.

    Raises:
        ValidationFailedError: If the target is not publicly routable.
    """
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        msg = f"target host {host!r} is local; set PULSEGRID_ALLOW_PRIVATE_TARGETS to allow it"
        raise ValidationFailedError(msg)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        msg = f"target address {host} is not publicly routable"
        raise ValidationFailedError(msg)


class MonitorService:
    """Create, read, update and delete monitors.

    Args:
        uow_factory: Creates a unit of work per operation.
        clock: Time source.
        allow_private_targets: Permit loopback and private-network targets.
    """

    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: Clock, *, allow_private_targets: bool
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._allow_private_targets = allow_private_targets

    async def create(self, data: MonitorCreate) -> Monitor:
        """Register a monitor; it becomes due immediately.

        Raises:
            ValidationFailedError: If the target is not allowed.
            DuplicateMonitorError: If the name is taken.
        """
        url = str(data.url)
        self._check_target(url)
        now = self._clock.now()
        monitor = Monitor(
            id=uuid4(),
            name=data.name,
            url=url,
            method=data.method,
            interval_seconds=data.interval_seconds,
            timeout_seconds=data.timeout_seconds,
            expected_status=data.expected_status,
            failure_threshold=data.failure_threshold,
            is_active=True,
            status=MonitorStatus.UNKNOWN,
            consecutive_failures=0,
            last_checked_at=None,
            next_check_at=now,
            created_at=now,
            updated_at=now,
        )
        async with self._uow_factory() as uow:
            await uow.monitors.add(monitor)
            await uow.commit()
        logger.info("monitor_created", monitor_id=str(monitor.id), name=monitor.name)
        return monitor

    async def get(self, monitor_id: UUID) -> Monitor:
        """Return a monitor.

        Raises:
            MonitorNotFoundError: If it does not exist.
        """
        async with self._uow_factory() as uow:
            monitor = await uow.monitors.get(monitor_id)
        if monitor is None:
            raise MonitorNotFoundError(monitor_id)
        return monitor

    async def list_monitors(self, *, limit: int, offset: int) -> list[Monitor]:
        """Return a page of monitors ordered by name."""
        async with self._uow_factory() as uow:
            return await uow.monitors.list_page(limit=limit, offset=offset)

    async def update(self, monitor_id: UUID, changes: MonitorUpdate) -> Monitor:
        """Apply a partial update.

        Raises:
            MonitorNotFoundError: If the monitor does not exist.
            ValidationFailedError: If the result breaks a business rule.
            DuplicateMonitorError: If the new name is taken.
        """
        async with self._uow_factory() as uow:
            monitor = await uow.monitors.get(monitor_id)
            if monitor is None:
                raise MonitorNotFoundError(monitor_id)
            self._apply(monitor, changes)
            await uow.monitors.update(monitor)
            await uow.commit()
        logger.info(
            "monitor_updated",
            monitor_id=str(monitor_id),
            fields=sorted(changes.model_dump(exclude_unset=True, exclude_none=True)),
        )
        return monitor

    async def delete(self, monitor_id: UUID) -> None:
        """Delete a monitor together with its checks and incidents.

        Raises:
            MonitorNotFoundError: If it does not exist.
        """
        async with self._uow_factory() as uow:
            deleted = await uow.monitors.delete(monitor_id)
            await uow.commit()
        if not deleted:
            raise MonitorNotFoundError(monitor_id)
        logger.info("monitor_deleted", monitor_id=str(monitor_id))

    def _apply(self, monitor: Monitor, changes: MonitorUpdate) -> None:
        now = self._clock.now()
        if changes.name is not None:
            monitor.name = changes.name
        if changes.url is not None:
            url = str(changes.url)
            self._check_target(url)
            monitor.url = url
        if changes.method is not None:
            monitor.method = HttpMethod(changes.method)
        if changes.timeout_seconds is not None:
            monitor.timeout_seconds = changes.timeout_seconds
        if changes.expected_status is not None:
            monitor.expected_status = changes.expected_status
        if changes.failure_threshold is not None:
            monitor.failure_threshold = changes.failure_threshold
        if changes.interval_seconds is not None:
            monitor.interval_seconds = changes.interval_seconds
            earliest = (monitor.last_checked_at or now) + timedelta(
                seconds=monitor.interval_seconds
            )
            monitor.next_check_at = min(monitor.next_check_at, earliest)
        if changes.is_active is not None:
            if changes.is_active and not monitor.is_active:
                monitor.next_check_at = now
            monitor.is_active = changes.is_active
        if monitor.timeout_seconds >= monitor.interval_seconds:
            msg = "timeout_seconds must be shorter than interval_seconds"
            raise ValidationFailedError(msg)
        monitor.updated_at = now

    def _check_target(self, url: str) -> None:
        if not self._allow_private_targets:
            ensure_public_target(url)
