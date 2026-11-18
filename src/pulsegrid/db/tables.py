"""ORM table definitions. Mapped to and from domain entities in ``repositories``."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, SmallInteger, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pulsegrid.db.base import Base
from pulsegrid.db.types import UTCDateTime


class MonitorRow(Base):
    """A registered HTTP endpoint and its current health state."""

    __tablename__ = "monitors"
    __table_args__ = (Index("ix_monitors_due", "is_active", "next_check_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    url: Mapped[str] = mapped_column(String(2048))
    method: Mapped[str] = mapped_column(String(8))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[float] = mapped_column(Float)
    expected_status: Mapped[int] = mapped_column(SmallInteger)
    failure_threshold: Mapped[int] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16))
    consecutive_failures: Mapped[int] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_check_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class CheckResultRow(Base):
    """The outcome of a single probe."""

    __tablename__ = "check_results"
    __table_args__ = (Index("ix_check_results_monitor_checked", "monitor_id", "checked_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    monitor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("monitors.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime)
    success: Mapped[bool] = mapped_column(Boolean)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class IncidentRow(Base):
    """A period during which a monitor was considered down."""

    __tablename__ = "incidents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    monitor_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("monitors.id", ondelete="CASCADE"), index=True
    )
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    cause: Mapped[str] = mapped_column(String(500))
