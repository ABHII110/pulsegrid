"""Injectable time source so business logic stays deterministic under test."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""


class SystemClock:
    """Clock backed by the operating system."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)
