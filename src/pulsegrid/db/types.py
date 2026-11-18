"""Custom SQLAlchemy column types."""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime column that always stores and returns UTC.

    PostgreSQL keeps the offset natively; SQLite drops it, so values read back from SQLite are
    re-tagged as UTC. Naive datetimes are rejected on write to catch bugs early.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Normalise outgoing values to UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            msg = "naive datetimes are not allowed; use timezone-aware UTC values"
            raise ValueError(msg)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Return values from the database as timezone-aware UTC datetimes."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
