import asyncio
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from pydantic import SecretStr
from typer.testing import CliRunner

from pulsegrid.cli import main as cli_main
from pulsegrid.container import build_container
from pulsegrid.db import migrate
from pulsegrid.integrations.locks import InMemoryLock, RedisLock
from pulsegrid.integrations.notifiers import LoggingNotifier, WebhookNotifier
from pulsegrid.workers.runner import run_worker
from tests.factories import make_settings

pytestmark = pytest.mark.integration


async def test_default_wiring_uses_local_adapters(tmp_path: Path) -> None:
    container = build_container(make_settings(tmp_path))

    assert container.redis is None
    assert isinstance(container.check_service._lock, InMemoryLock)
    assert isinstance(container.check_service._notifier, LoggingNotifier)
    await container.aclose()
    assert container.http_client.is_closed


async def test_redis_and_webhook_wiring(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        redis_url=SecretStr("redis://localhost:6390/0"),
        webhook_url="https://hooks.example.com/pulsegrid",
        webhook_secret=SecretStr("s3cret"),
    )
    container = build_container(settings)

    assert container.redis is not None
    assert isinstance(container.check_service._lock, RedisLock)
    notifier = container.check_service._notifier
    assert isinstance(notifier, WebhookNotifier)
    assert notifier._url == "https://hooks.example.com/pulsegrid"
    assert notifier._secret == "s3cret"
    await container.aclose()


async def test_run_worker_once(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    await asyncio.to_thread(migrate.upgrade, settings)

    assert await run_worker(settings, once=True) == 0


async def test_run_worker_loop_cleans_up_on_cancel(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, worker_poll_interval_seconds=0.01)
    await asyncio.to_thread(migrate.upgrade, settings)

    task = asyncio.create_task(run_worker(settings, once=False))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_serve_passes_settings_to_uvicorn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(app: object, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("PULSEGRID_API_KEY", "k" * 32)
    monkeypatch.setenv(
        "PULSEGRID_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 's.db').as_posix()}"
    )

    result = CliRunner().invoke(cli_main.app, ["serve", "--port", "9100"])

    assert result.exit_code == 0, result.output
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9100
    assert calls[0]["access_log"] is False
