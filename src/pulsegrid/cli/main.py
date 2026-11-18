"""``pulsegrid`` command-line interface. Prints JSON on stdout and errors on stderr."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from typing import Annotated, NoReturn
from uuid import UUID

import typer
import uvicorn
from pydantic import BaseModel, ValidationError

from pulsegrid.container import Container, container_scope
from pulsegrid.core.config import Settings, get_settings
from pulsegrid.core.exceptions import (
    ConflictError,
    NotFoundError,
    PulseGridError,
    ValidationFailedError,
)
from pulsegrid.core.logging import configure_logging
from pulsegrid.db import migrate
from pulsegrid.domain.models import HttpMethod
from pulsegrid.domain.schemas import (
    CheckResultRead,
    IncidentRead,
    MonitorCreate,
    MonitorRead,
    MonitorUpdate,
    UptimeReportRead,
)
from pulsegrid.workers.runner import run_worker

EXIT_ERROR = 1
EXIT_INVALID = 2
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5

app = typer.Typer(
    name="pulsegrid",
    help="PulseGrid uptime monitoring.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
monitors_app = typer.Typer(help="Manage monitors.", no_args_is_help=True)
report_app = typer.Typer(help="Uptime and incident reports.", no_args_is_help=True)
db_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
app.add_typer(monitors_app, name="monitors")
app.add_typer(report_app, name="report")
app.add_typer(db_app, name="db")

MonitorId = Annotated[UUID, typer.Argument(help="Monitor ID.")]


def exit_code_for(error: PulseGridError) -> int:
    """CLI exit code for a PulseGrid error."""
    if isinstance(error, ValidationFailedError):
        return EXIT_INVALID
    if isinstance(error, NotFoundError):
        return EXIT_NOT_FOUND
    if isinstance(error, ConflictError):
        return EXIT_CONFLICT
    return EXIT_ERROR


def _fail(message: str, code: int) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code)


def _settings() -> Settings:
    try:
        return get_settings()
    except ValidationError as exc:
        _fail(f"invalid configuration: {exc.errors(include_url=False)}", EXIT_INVALID)


def _run[T](action: Callable[[Container], Awaitable[T]]) -> T:
    settings = _settings()

    async def runner() -> T:
        async with container_scope(settings) as container:
            return await action(container)

    try:
        return asyncio.run(runner())
    except PulseGridError as exc:
        _fail(exc.message, exit_code_for(exc))


def _validate[M: BaseModel](model: type[M], data: dict[str, object]) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or 'input'}: {err['msg']}"
            for err in exc.errors()
        )
        _fail(problems, EXIT_INVALID)


def _emit(payload: BaseModel | Sequence[BaseModel]) -> None:
    if isinstance(payload, BaseModel):
        typer.echo(payload.model_dump_json(indent=2))
    else:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in payload], indent=2))


@app.callback()
def main() -> None:
    """Configure logging before any command runs."""
    settings = _settings()
    configure_logging(settings.log_level, json_output=settings.log_json)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address.")] = None,
    port: Annotated[int | None, typer.Option(min=1, max=65535, help="Bind port.")] = None,
) -> None:
    """Start the REST API."""
    from pulsegrid.api.app import create_app

    settings = _settings()
    if settings.migrate_on_start:
        migrate.upgrade(settings)
    try:
        api = create_app(settings)
    except PulseGridError as exc:
        _fail(exc.message, exit_code_for(exc))
    uvicorn.run(
        api,
        host=host or settings.api_host,
        port=port or settings.api_port,
        access_log=False,
        log_level=settings.log_level.lower(),
    )


@app.command()
def worker(
    once: Annotated[bool, typer.Option("--once", help="Run a single pass and exit.")] = False,
) -> None:
    """Run the probe scheduler until interrupted."""
    settings = _settings()
    try:
        completed = asyncio.run(run_worker(settings, once=once))
    except KeyboardInterrupt:
        return
    except PulseGridError as exc:
        _fail(exc.message, exit_code_for(exc))
    if once:
        typer.echo(json.dumps({"checks_completed": completed}))


@app.command()
def prune(
    days: Annotated[int, typer.Option(min=1, help="Delete check results older than this.")],
) -> None:
    """Delete old check results."""
    deleted = _run(lambda c: c.reporting_service.prune_checks(older_than=timedelta(days=days)))
    typer.echo(json.dumps({"deleted": deleted}))


# --- db ---------------------------------------------------------------------------------------


@db_app.command("upgrade")
def db_upgrade(
    revision: Annotated[str, typer.Argument(help="Target revision.")] = "head",
) -> None:
    """Apply migrations."""
    migrate.upgrade(_settings(), revision)
    typer.echo(json.dumps({"migrated_to": revision}))


@db_app.command("downgrade")
def db_downgrade(
    revision: Annotated[str, typer.Argument(help="Target revision, e.g. 'base'.")],
) -> None:
    """Roll back migrations."""
    migrate.downgrade(_settings(), revision)
    typer.echo(json.dumps({"migrated_to": revision}))


# --- monitors ---------------------------------------------------------------------------------


@monitors_app.command("add")
def monitors_add(
    name: Annotated[str, typer.Argument(help="Unique monitor name.")],
    url: Annotated[str, typer.Argument(help="http(s) URL to probe.")],
    method: Annotated[HttpMethod, typer.Option(help="HTTP method.")] = HttpMethod.GET,
    interval: Annotated[int, typer.Option(help="Seconds between checks.")] = 60,
    timeout: Annotated[float, typer.Option(help="Probe timeout in seconds.")] = 10.0,
    expected_status: Annotated[int, typer.Option(help="Status code meaning healthy.")] = 200,
    failure_threshold: Annotated[
        int, typer.Option(help="Consecutive failures before an incident opens.")
    ] = 3,
) -> None:
    """Register a monitor."""
    data = _validate(
        MonitorCreate,
        {
            "name": name,
            "url": url,
            "method": method,
            "interval_seconds": interval,
            "timeout_seconds": timeout,
            "expected_status": expected_status,
            "failure_threshold": failure_threshold,
        },
    )
    _emit(MonitorRead.model_validate(_run(lambda c: c.monitor_service.create(data))))


@monitors_app.command("list")
def monitors_list(
    limit: Annotated[int, typer.Option(min=1, max=500)] = 100,
    offset: Annotated[int, typer.Option(min=0)] = 0,
) -> None:
    """List monitors."""
    monitors = _run(lambda c: c.monitor_service.list_monitors(limit=limit, offset=offset))
    _emit([MonitorRead.model_validate(monitor) for monitor in monitors])


@monitors_app.command("show")
def monitors_show(monitor_id: MonitorId) -> None:
    """Show one monitor."""
    _emit(MonitorRead.model_validate(_run(lambda c: c.monitor_service.get(monitor_id))))


@monitors_app.command("pause")
def monitors_pause(monitor_id: MonitorId) -> None:
    """Stop checking a monitor."""
    changes = MonitorUpdate(is_active=False)
    monitor = _run(lambda c: c.monitor_service.update(monitor_id, changes))
    _emit(MonitorRead.model_validate(monitor))


@monitors_app.command("resume")
def monitors_resume(monitor_id: MonitorId) -> None:
    """Resume checking a monitor; it is checked on the next worker pass."""
    changes = MonitorUpdate(is_active=True)
    monitor = _run(lambda c: c.monitor_service.update(monitor_id, changes))
    _emit(MonitorRead.model_validate(monitor))


@monitors_app.command("remove")
def monitors_remove(monitor_id: MonitorId) -> None:
    """Delete a monitor and its history."""
    _run(lambda c: c.monitor_service.delete(monitor_id))
    typer.echo(json.dumps({"deleted": str(monitor_id)}))


@monitors_app.command("check")
def monitors_check(monitor_id: MonitorId) -> None:
    """Probe a monitor now and print the result."""
    _emit(CheckResultRead.model_validate(_run(lambda c: c.check_service.run_check(monitor_id))))


# --- report -----------------------------------------------------------------------------------


@report_app.command("uptime")
def report_uptime(
    monitor_id: MonitorId,
    hours: Annotated[int, typer.Option(min=1, max=8760, help="Window size.")] = 24,
) -> None:
    """Show uptime over the trailing window."""
    report = _run(lambda c: c.reporting_service.uptime(monitor_id, window=timedelta(hours=hours)))
    _emit(UptimeReportRead.model_validate(report))


@report_app.command("incidents")
def report_incidents(
    monitor: Annotated[UUID | None, typer.Option(help="Only this monitor.")] = None,
    open_only: Annotated[bool, typer.Option("--open-only", help="Only unresolved.")] = False,
    limit: Annotated[int, typer.Option(min=1, max=500)] = 50,
) -> None:
    """List incidents, newest first."""
    incidents = _run(
        lambda c: c.reporting_service.incidents(
            monitor_id=monitor, open_only=open_only, limit=limit
        )
    )
    _emit([IncidentRead.model_validate(incident) for incident in incidents])
