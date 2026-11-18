"""Monitor management, manual checks and per-monitor reports."""

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pulsegrid.api.deps import CheckServiceDep, MonitorServiceDep, ReportingServiceDep
from pulsegrid.api.security import require_api_key
from pulsegrid.domain.schemas import (
    CheckResultRead,
    IncidentRead,
    MonitorCreate,
    MonitorRead,
    MonitorUpdate,
    UptimeReportRead,
)

router = APIRouter(prefix="/monitors", tags=["monitors"], dependencies=[Depends(require_api_key)])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_monitor(payload: MonitorCreate, service: MonitorServiceDep) -> MonitorRead:
    """Register a monitor; it is checked on the next worker pass."""
    return MonitorRead.model_validate(await service.create(payload))


@router.get("")
async def list_monitors(
    service: MonitorServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MonitorRead]:
    """List monitors ordered by name."""
    monitors = await service.list_monitors(limit=limit, offset=offset)
    return [MonitorRead.model_validate(monitor) for monitor in monitors]


@router.get("/{monitor_id}")
async def get_monitor(monitor_id: UUID, service: MonitorServiceDep) -> MonitorRead:
    """Return one monitor."""
    return MonitorRead.model_validate(await service.get(monitor_id))


@router.patch("/{monitor_id}")
async def update_monitor(
    monitor_id: UUID, payload: MonitorUpdate, service: MonitorServiceDep
) -> MonitorRead:
    """Partially update a monitor (including pausing via ``is_active``)."""
    return MonitorRead.model_validate(await service.update(monitor_id, payload))


@router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_monitor(monitor_id: UUID, service: MonitorServiceDep) -> None:
    """Delete a monitor with its history."""
    await service.delete(monitor_id)


@router.post("/{monitor_id}/check")
async def run_check(monitor_id: UUID, service: CheckServiceDep) -> CheckResultRead:
    """Probe the monitor immediately and return the recorded result."""
    return CheckResultRead.model_validate(await service.run_check(monitor_id))


@router.get("/{monitor_id}/checks")
async def list_checks(
    monitor_id: UUID,
    service: ReportingServiceDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
) -> list[CheckResultRead]:
    """Return the most recent check results, newest first."""
    results = await service.recent_checks(monitor_id, limit=limit)
    return [CheckResultRead.model_validate(result) for result in results]


@router.get("/{monitor_id}/uptime")
async def uptime(
    monitor_id: UUID,
    service: ReportingServiceDep,
    window_hours: Annotated[int, Query(ge=1, le=8760)] = 24,
) -> UptimeReportRead:
    """Return uptime statistics for the trailing window."""
    report = await service.uptime(monitor_id, window=timedelta(hours=window_hours))
    return UptimeReportRead.model_validate(report)


@router.get("/{monitor_id}/incidents")
async def monitor_incidents(
    monitor_id: UUID,
    service: ReportingServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[IncidentRead]:
    """Return the monitor's incidents, newest first."""
    incidents = await service.incidents(monitor_id=monitor_id, limit=limit)
    return [IncidentRead.model_validate(incident) for incident in incidents]
