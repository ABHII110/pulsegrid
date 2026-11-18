"""Polls for due monitors and checks them with bounded concurrency."""

import asyncio
import contextlib

from pulsegrid.core.clock import Clock
from pulsegrid.core.exceptions import PulseGridError
from pulsegrid.core.logging import get_logger
from pulsegrid.domain.models import Monitor
from pulsegrid.domain.ports import UnitOfWorkFactory
from pulsegrid.services.check_service import CheckService

logger = get_logger(__name__)


class Scheduler:
    """Drives periodic checks.

    Args:
        uow_factory: Creates a unit of work for loading due monitors.
        check_service: Performs and records each check.
        clock: Time source.
        poll_interval_seconds: Pause between polling passes.
        concurrency: Maximum checks in flight at once.
        batch_size: Maximum monitors loaded per pass.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        check_service: CheckService,
        clock: Clock,
        *,
        poll_interval_seconds: float,
        concurrency: int,
        batch_size: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._check_service = check_service
        self._clock = clock
        self._poll_interval = poll_interval_seconds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._batch_size = batch_size

    async def run_once(self) -> int:
        """Check every monitor currently due; return how many checks were recorded."""
        async with self._uow_factory() as uow:
            due = await uow.monitors.list_due(self._clock.now(), limit=self._batch_size)
        if not due:
            return 0
        outcomes = await asyncio.gather(*(self._check(monitor) for monitor in due))
        completed = sum(outcomes)
        logger.info("scheduler_pass", due=len(due), completed=completed)
        return completed

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Run passes until ``stop`` is set, surviving individual pass failures."""
        logger.info("scheduler_started", poll_interval_seconds=self._poll_interval)
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("scheduler_pass_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
        logger.info("scheduler_stopped")

    async def _check(self, monitor: Monitor) -> bool:
        async with self._semaphore:
            try:
                result = await self._check_service.check_monitor(monitor, force=False)
            except PulseGridError as exc:
                logger.error(
                    "check_failed", monitor_id=str(monitor.id), code=exc.code, error=exc.message
                )
                return False
        return result is not None
