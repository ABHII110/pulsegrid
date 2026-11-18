"""FastAPI application factory."""

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

API_PREFIX = "/api/v1"


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
        if container is not None:
            yield
            return
        owned = build_container(settings)
        app.state.container = owned
        try:
            yield
        finally:
            await owned.aclose()

    is_production = settings.environment is Environment.PRODUCTION
    app = FastAPI(
        title="PulseGrid",
        version=__version__,
        summary="Uptime monitoring: scheduled probes, incidents, uptime reports and webhooks.",
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
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
