"""Incident listing across all monitors."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from pulsegrid.api.deps import ReportingServiceDep
from pulsegrid.api.security import require_api_key
from pulsegrid.domain.schemas import IncidentRead

router = APIRouter(prefix="/incidents", tags=["incidents"], dependencies=[Depends(require_api_key)])


@router.get("")
async def list_incidents(
    service: ReportingServiceDep,
    open_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[IncidentRead]:
    """Return incidents newest first, optionally only unresolved ones."""
    incidents = await service.incidents(open_only=open_only, limit=limit)
    return [IncidentRead.model_validate(incident) for incident in incidents]
