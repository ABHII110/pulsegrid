"""Process entry point for the probe worker."""

import asyncio
import contextlib
import signal

from pulsegrid.container import container_scope
from pulsegrid.core.config import Settings
from pulsegrid.core.logging import get_logger

logger = get_logger(__name__)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Not supported on Windows; Ctrl+C then surfaces as KeyboardInterrupt instead.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)


async def run_worker(settings: Settings, *, once: bool) -> int:
    """Run the scheduler.

    Args:
        settings: Runtime configuration.
        once: Run a single pass and return instead of looping until signalled.

    Returns:
        Number of checks recorded by the single pass, or ``0`` after a graceful shutdown.
    """
    async with container_scope(settings) as container:
        if once:
            return await container.scheduler.run_once()
        stop = asyncio.Event()
        _install_signal_handlers(stop)
        await container.scheduler.run_forever(stop)
    logger.info("worker_exited")
    return 0
