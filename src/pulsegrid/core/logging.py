"""Structured logging built on structlog."""

import logging
import sys
from typing import cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor

from pulsegrid.core.config import LogLevel


def configure_logging(level: LogLevel, *, json_output: bool) -> None:
    """Configure structlog for the whole process.

    Args:
        level: Minimum level to emit.
        json_output: Emit one JSON object per line when true, coloured console output otherwise.
    """
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if json_output:
        processors += [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> FilteringBoundLogger:
    """Return a lazily configured structured logger tagged with ``logger_name=name``.

    The logger resolves its configuration on first use, so module-level loggers created at
    import time still honour a later ``configure_logging`` call.
    """
    return cast(FilteringBoundLogger, structlog.get_logger(logger_name=name))
