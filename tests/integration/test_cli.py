import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from pulsegrid.cli.main import app, exit_code_for
from pulsegrid.core.config import get_settings
from pulsegrid.core.exceptions import (
    CheckInProgressError,
    MonitorNotFoundError,
    PulseGridError,
    RepositoryError,
    ValidationFailedError,
)

pytestmark = pytest.mark.integration


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/ok" else 500)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def http_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CliRunner:
    monkeypatch.setenv(
        "PULSEGRID_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'cli.db').as_posix()}"
    )
    monkeypatch.setenv("PULSEGRID_ALLOW_PRIVATE_TARGETS", "true")
    monkeypatch.setenv("PULSEGRID_LOG_LEVEL", "WARNING")
    runner = CliRunner()
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 0, result.output
    return runner


def _json(cli: CliRunner, *args: str) -> Any:
    result = cli.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_full_monitor_workflow(cli: CliRunner, http_server: str) -> None:
    created = _json(cli, "monitors", "add", "local", f"{http_server}/ok", "--interval", "30")
    monitor_id = created["id"]
    assert created["interval_seconds"] == 30

    assert [m["name"] for m in _json(cli, "monitors", "list")] == ["local"]
    assert _json(cli, "monitors", "show", monitor_id)["id"] == monitor_id

    check = _json(cli, "monitors", "check", monitor_id)
    assert check["success"] is True
    assert check["status_code"] == 200

    assert _json(cli, "monitors", "pause", monitor_id)["is_active"] is False
    assert _json(cli, "monitors", "resume", monitor_id)["is_active"] is True

    uptime = _json(cli, "report", "uptime", monitor_id, "--hours", "1")
    assert uptime["uptime_percent"] == 100.0

    assert _json(cli, "worker", "--once") == {"checks_completed": 1}
    assert _json(cli, "report", "incidents", "--monitor", monitor_id) == []
    assert _json(cli, "prune", "--days", "1") == {"deleted": 0}

    assert _json(cli, "monitors", "remove", monitor_id) == {"deleted": monitor_id}
    missing = cli.invoke(app, ["monitors", "show", monitor_id])
    assert missing.exit_code == 4
    assert "not found" in missing.stderr


def test_failing_target_opens_incident(cli: CliRunner, http_server: str) -> None:
    created = _json(
        cli, "monitors", "add", "broken", f"{http_server}/fail", "--failure-threshold", "1"
    )

    check = _json(cli, "monitors", "check", created["id"])
    incidents = _json(cli, "report", "incidents", "--open-only")

    assert check["success"] is False
    assert check["error"] == "expected status 200, got 500"
    assert [i["monitor_id"] for i in incidents] == [created["id"]]


def test_invalid_input_exits_2(cli: CliRunner) -> None:
    result = cli.invoke(app, ["monitors", "add", "x", "ftp://example.com"])

    assert result.exit_code == 2
    assert "url" in result.stderr


def test_duplicate_name_exits_5(cli: CliRunner, http_server: str) -> None:
    _json(cli, "monitors", "add", "dup", f"{http_server}/ok")
    result = cli.invoke(app, ["monitors", "add", "dup", f"{http_server}/ok"])

    assert result.exit_code == 5


def test_private_targets_blocked_by_default(
    cli: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PULSEGRID_ALLOW_PRIVATE_TARGETS", "false")
    get_settings.cache_clear()
    result = cli.invoke(app, ["monitors", "add", "local", "http://127.0.0.1/"])

    assert result.exit_code == 2


def test_invalid_configuration_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULSEGRID_WORKER_CONCURRENCY", "0")
    result = CliRunner().invoke(app, ["monitors", "list"])

    assert result.exit_code == 2
    assert "invalid configuration" in result.stderr


def test_serve_refuses_to_start_without_api_key(cli: CliRunner) -> None:
    result = cli.invoke(app, ["serve"])

    assert result.exit_code == 1
    assert "PULSEGRID_API_KEY" in result.stderr


def test_db_downgrade(cli: CliRunner) -> None:
    assert _json(cli, "db", "downgrade", "base") == {"migrated_to": "base"}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ValidationFailedError("x"), 2),
        (MonitorNotFoundError(uuid4()), 4),
        (CheckInProgressError(uuid4()), 5),
        (RepositoryError("x"), 1),
        (PulseGridError("x"), 1),
    ],
)
def test_exit_codes(error: PulseGridError, code: int) -> None:
    assert exit_code_for(error) == code
