"""SQLAlchemy implementations of the repository ports and the unit of work."""

from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pulsegrid.core.exceptions import DuplicateMonitorError, RepositoryError
from pulsegrid.db.tables import CheckResultRow, IncidentRow, MonitorRow
from pulsegrid.domain.models import (
    CheckResult,
    CheckSummary,
    HttpMethod,
    Incident,
    Monitor,
    MonitorStatus,
)
from pulsegrid.domain.ports import CheckResultRepository, IncidentRepository, MonitorRepository

_MONITOR_FIELDS = (
    "name",
    "url",
    "interval_seconds",
    "timeout_seconds",
    "expected_status",
    "failure_threshold",
    "is_active",
    "consecutive_failures",
    "last_checked_at",
    "next_check_at",
    "created_at",
    "updated_at",
)


def _monitor_to_domain(row: MonitorRow) -> Monitor:
    return Monitor(
        id=row.id,
        name=row.name,
        url=row.url,
        method=HttpMethod(row.method),
        interval_seconds=row.interval_seconds,
        timeout_seconds=row.timeout_seconds,
        expected_status=row.expected_status,
        failure_threshold=row.failure_threshold,
        is_active=row.is_active,
        status=MonitorStatus(row.status),
        consecutive_failures=row.consecutive_failures,
        last_checked_at=row.last_checked_at,
        next_check_at=row.next_check_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _copy_monitor_to_row(monitor: Monitor, row: MonitorRow) -> None:
    for field in _MONITOR_FIELDS:
        setattr(row, field, getattr(monitor, field))
    row.method = monitor.method.value
    row.status = monitor.status.value


def _check_to_domain(row: CheckResultRow) -> CheckResult:
    return CheckResult(
        id=row.id,
        monitor_id=row.monitor_id,
        checked_at=row.checked_at,
        success=row.success,
        status_code=row.status_code,
        latency_ms=row.latency_ms,
        error=row.error,
    )


def _incident_to_domain(row: IncidentRow) -> Incident:
    return Incident(
        id=row.id,
        monitor_id=row.monitor_id,
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        cause=row.cause,
    )


class SqlMonitorRepository:
    """Monitor persistence backed by an ``AsyncSession``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, monitor: Monitor) -> None:
        """Insert a monitor."""
        row = MonitorRow(id=monitor.id)
        _copy_monitor_to_row(monitor, row)
        self._session.add(row)
        await self._flush_unique_name(monitor.name)

    async def get(self, monitor_id: UUID) -> Monitor | None:
        """Return the monitor or ``None``."""
        row = await self._session.get(MonitorRow, monitor_id)
        return None if row is None else _monitor_to_domain(row)

    async def list_page(self, *, limit: int, offset: int) -> list[Monitor]:
        """Return monitors ordered by name."""
        stmt = select(MonitorRow).order_by(MonitorRow.name).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars()
        return [_monitor_to_domain(row) for row in rows]

    async def list_due(self, now: datetime, *, limit: int) -> list[Monitor]:
        """Return due monitors, the most overdue first."""
        stmt = (
            select(MonitorRow)
            .where(MonitorRow.is_active.is_(True), MonitorRow.next_check_at <= now)
            .order_by(MonitorRow.next_check_at)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_monitor_to_domain(row) for row in rows]

    async def update(self, monitor: Monitor) -> None:
        """Persist changes to an existing monitor."""
        row = await self._session.get(MonitorRow, monitor.id)
        if row is None:
            msg = f"cannot update missing monitor {monitor.id}"
            raise RepositoryError(msg)
        _copy_monitor_to_row(monitor, row)
        await self._flush_unique_name(monitor.name)

    async def delete(self, monitor_id: UUID) -> bool:
        """Delete the monitor; check results and incidents cascade."""
        row = await self._session.get(MonitorRow, monitor_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def _flush_unique_name(self, name: str) -> None:
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise DuplicateMonitorError(name) from exc


class SqlCheckResultRepository:
    """Check result persistence backed by an ``AsyncSession``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, result: CheckResult) -> None:
        """Insert a check result."""
        self._session.add(
            CheckResultRow(
                id=result.id,
                monitor_id=result.monitor_id,
                checked_at=result.checked_at,
                success=result.success,
                status_code=result.status_code,
                latency_ms=result.latency_ms,
                error=result.error,
            )
        )
        await self._session.flush()

    async def list_recent(self, monitor_id: UUID, *, limit: int) -> list[CheckResult]:
        """Return the newest results first."""
        stmt = (
            select(CheckResultRow)
            .where(CheckResultRow.monitor_id == monitor_id)
            .order_by(CheckResultRow.checked_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_check_to_domain(row) for row in rows]

    async def summarize(
        self, monitor_id: UUID, *, since: datetime, until: datetime
    ) -> CheckSummary:
        """Count and average results inside the window."""
        stmt = select(
            func.count(CheckResultRow.id),
            func.coalesce(func.sum(case((CheckResultRow.success.is_(True), 1), else_=0)), 0),
            func.avg(CheckResultRow.latency_ms),
        ).where(
            CheckResultRow.monitor_id == monitor_id,
            CheckResultRow.checked_at >= since,
            CheckResultRow.checked_at <= until,
        )
        total, successful, avg_latency = (await self._session.execute(stmt)).one()
        return CheckSummary(
            total=int(total),
            successful=int(successful),
            avg_latency_ms=None if avg_latency is None else round(float(avg_latency), 3),
        )

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Delete results checked before ``cutoff``."""
        stmt = delete(CheckResultRow).where(CheckResultRow.checked_at < cutoff)
        result = await self._session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)


class SqlIncidentRepository:
    """Incident persistence backed by an ``AsyncSession``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, incident: Incident) -> None:
        """Insert an incident."""
        self._session.add(
            IncidentRow(
                id=incident.id,
                monitor_id=incident.monitor_id,
                opened_at=incident.opened_at,
                resolved_at=incident.resolved_at,
                cause=incident.cause,
            )
        )
        await self._session.flush()

    async def get_open_for_monitor(self, monitor_id: UUID) -> Incident | None:
        """Return the newest unresolved incident for the monitor."""
        stmt = (
            select(IncidentRow)
            .where(IncidentRow.monitor_id == monitor_id, IncidentRow.resolved_at.is_(None))
            .order_by(IncidentRow.opened_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _incident_to_domain(row)

    async def update(self, incident: Incident) -> None:
        """Persist the incident's resolution time and cause."""
        row = await self._session.get(IncidentRow, incident.id)
        if row is None:
            msg = f"cannot update missing incident {incident.id}"
            raise RepositoryError(msg)
        row.resolved_at = incident.resolved_at
        row.cause = incident.cause
        await self._session.flush()

    async def list_recent(
        self, *, monitor_id: UUID | None, open_only: bool, limit: int
    ) -> list[Incident]:
        """Return incidents newest first."""
        stmt = select(IncidentRow).order_by(IncidentRow.opened_at.desc()).limit(limit)
        if monitor_id is not None:
            stmt = stmt.where(IncidentRow.monitor_id == monitor_id)
        if open_only:
            stmt = stmt.where(IncidentRow.resolved_at.is_(None))
        rows = (await self._session.execute(stmt)).scalars()
        return [_incident_to_domain(row) for row in rows]


class SqlAlchemyUnitOfWork:
    """Unit of work over a single ``AsyncSession``.

    Any SQLAlchemy error escaping the ``async with`` block is rolled back and re-raised as
    ``RepositoryError`` so callers never see driver-specific exceptions.

    Args:
        session_factory: Factory creating the session for this unit of work.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._monitors: MonitorRepository | None = None
        self._checks: CheckResultRepository | None = None
        self._incidents: IncidentRepository | None = None

    @property
    def monitors(self) -> MonitorRepository:
        """Monitor repository bound to this transaction."""
        if self._monitors is None:
            raise _not_entered()
        return self._monitors

    @property
    def checks(self) -> CheckResultRepository:
        """Check result repository bound to this transaction."""
        if self._checks is None:
            raise _not_entered()
        return self._checks

    @property
    def incidents(self) -> IncidentRepository:
        """Incident repository bound to this transaction."""
        if self._incidents is None:
            raise _not_entered()
        return self._incidents

    async def commit(self) -> None:
        """Commit the transaction."""
        if self._session is None:
            raise _not_entered()
        await self._session.commit()

    async def __aenter__(self) -> Self:
        session = self._session_factory()
        self._session = session
        self._monitors = SqlMonitorRepository(session)
        self._checks = SqlCheckResultRepository(session)
        self._incidents = SqlIncidentRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self._session
        self._session = self._monitors = self._checks = self._incidents = None
        if session is None:
            return
        try:
            await session.rollback()
        finally:
            await session.close()
        if isinstance(exc, SQLAlchemyError):
            msg = "database operation failed"
            raise RepositoryError(msg) from exc


def _not_entered() -> RepositoryError:
    return RepositoryError("unit of work used outside of its 'async with' block")
