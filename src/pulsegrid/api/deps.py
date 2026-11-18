"""FastAPI dependency providers resolving services from the container."""

from typing import Annotated, cast

from fastapi import Depends, Request

from pulsegrid.container import Container
from pulsegrid.services.check_service import CheckService
from pulsegrid.services.monitor_service import MonitorService
from pulsegrid.services.reporting_service import ReportingService


def get_container(request: Request) -> Container:
    """Return the container attached to the application."""
    return cast(Container, request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]


def get_monitor_service(container: ContainerDep) -> MonitorService:
    """Provide the monitor service."""
    return container.monitor_service


def get_check_service(container: ContainerDep) -> CheckService:
    """Provide the check service."""
    return container.check_service


def get_reporting_service(container: ContainerDep) -> ReportingService:
    """Provide the reporting service."""
    return container.reporting_service


MonitorServiceDep = Annotated[MonitorService, Depends(get_monitor_service)]
CheckServiceDep = Annotated[CheckService, Depends(get_check_service)]
ReportingServiceDep = Annotated[ReportingService, Depends(get_reporting_service)]
