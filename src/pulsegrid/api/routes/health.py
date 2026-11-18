"""Unauthenticated liveness and readiness probes."""

from fastapi import APIRouter

from pulsegrid.api.deps import ContainerDep
from pulsegrid.db.session import ping

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    """Report that the process is serving requests."""
    return {"status": "ok"}


@router.get("/readyz")
async def readiness(container: ContainerDep) -> dict[str, str]:
    """Report whether the database is reachable (503 otherwise)."""
    await ping(container.engine)
    return {"status": "ready"}
