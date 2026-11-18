"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pulsegrid import __version__
from pulsegrid.api.errors import register_exception_handlers
from pulsegrid.api.middleware import RequestContextMiddleware
from pulsegrid.api.routes import health, incidents, monitors, site
from pulsegrid.container import Container, build_container
from pulsegrid.core.config import Environment, Settings
from pulsegrid.core.exceptions import ConfigurationError
from pulsegrid.core.logging import get_logger

API_PREFIX = "/api/v1"

logger = get_logger(__name__)


def create_app(settings: Settings, *, container: Container | None = None) -> FastAPI:
    """Build the REST API.

    Args:
        settings: Runtime configuration; ``api_key`` must be set.
        container: Pre-built container to use (its lifecycle then stays with the caller).
            When omitted, one is built on startup and closed on shutdown.

    Raises:
        ConfigurationError: If no API key is configured.
    """
    if settings.api_key is None:
        msg = "PULSEGRID_API_KEY must be set to run the API"
        raise ConfigurationError(msg)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = None
        active = container
        if active is None:
            owned = active = build_container(settings)
            app.state.container = owned

        stop = asyncio.Event()
        worker = None
        if settings.embedded_worker:
            logger.info("embedded_worker_starting")
            worker = asyncio.create_task(active.scheduler.run_forever(stop))
        try:
            yield
        finally:
            if worker is not None:
                stop.set()
                await worker
            if owned is not None:
                await owned.aclose()

    show_docs = settings.environment is not Environment.PRODUCTION or settings.expose_docs
    app = FastAPI(
        title="PulseGrid",
        version=__version__,
        summary="Uptime monitoring: scheduled probes, incidents, uptime reports and webhooks.",
        lifespan=lifespan,
        docs_url="/docs" if show_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if show_docs else None,
    )
    if container is not None:
        app.state.container = container

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(site.router)
    app.include_router(health.router)
    app.include_router(monitors.router, prefix=API_PREFIX)
    app.include_router(incidents.router, prefix=API_PREFIX)
    return app
