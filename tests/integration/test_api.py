import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from pulsegrid.api.app import create_app
from pulsegrid.container import Container, build_container
from pulsegrid.core.config import Environment, Settings
from pulsegrid.core.exceptions import ConfigurationError
from pulsegrid.db.tables import MonitorRow
from tests.factories import TEST_API_KEY, make_settings
from tests.fakes import FakeClock

pytestmark = pytest.mark.integration

AUTH = {"X-API-Key": TEST_API_KEY}


class Upstream:
    """Fake monitored endpoint whose status code tests can change."""

    def __init__(self) -> None:
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self.status)


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
async def container(settings: Settings, upstream: Upstream) -> AsyncIterator[Container]:
    container = build_container(
        settings, transport=httpx.MockTransport(upstream), clock=FakeClock()
    )
    async with container.engine.begin() as connection:
        await connection.run_sync(MonitorRow.metadata.create_all)
    yield container
    await container.aclose()


@pytest.fixture
def app(settings: Settings, container: Container) -> FastAPI:
    return create_app(settings, container=container)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _create(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    body = {"name": "api", "url": "https://example.com/health", **overrides}
    response = await client.post("/api/v1/monitors", json=body, headers=AUTH)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_api_requires_api_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        create_app(make_settings(tmp_path, api_key=None))


async def test_landing_page_is_public_html(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<canvas class="planet-canvas"' in response.text
    assert "PulseGrid" in response.text
    assert "data-explore-open" in response.text  # space explorer entry point
    openapi = (await client.get("/openapi.json")).json()
    assert "/" not in openapi["paths"]


async def test_health_endpoints_need_no_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).json() == {"status": "ready"}


async def test_readiness_reports_database_outage(tmp_path: Path) -> None:
    unreachable = (tmp_path / "missing-dir" / "db.sqlite").as_posix()
    settings = make_settings(tmp_path, database_url=SecretStr(f"sqlite+aiosqlite:///{unreachable}"))
    container = build_container(settings)
    app = create_app(settings, container=container)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/readyz")
    await container.aclose()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong-key-wrong-key-wrong-key"}])
async def test_protected_routes_reject_bad_keys(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/monitors", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "ApiKey"
    body = response.json()["error"]
    assert body["code"] == "unauthorized"
    assert body["request_id"] == response.headers["X-Request-ID"]


async def test_monitor_crud(client: httpx.AsyncClient) -> None:
    created = await _create(client, interval_seconds=120)
    monitor_url = f"/api/v1/monitors/{created['id']}"

    assert created["status"] == "unknown"
    assert created["interval_seconds"] == 120
    assert (await client.get(monitor_url, headers=AUTH)).json() == created

    listing = await client.get("/api/v1/monitors", params={"limit": 10}, headers=AUTH)
    assert [m["id"] for m in listing.json()] == [created["id"]]

    patched = await client.patch(
        monitor_url, json={"name": "renamed", "is_active": False}, headers=AUTH
    )
    assert patched.status_code == 200
    assert (patched.json()["name"], patched.json()["is_active"]) == ("renamed", False)

    assert (await client.delete(monitor_url, headers=AUTH)).status_code == 204
    missing = await client.get(monitor_url, headers=AUTH)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


async def test_validation_errors_use_envelope(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/monitors", json={"name": "", "url": "ftp://x"}, headers=AUTH
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert {tuple(d["loc"]) for d in error["details"]} >= {("body", "name"), ("body", "url")}


async def test_business_rule_violations_are_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/monitors", json={"name": "x", "url": "http://10.0.0.1/"}, headers=AUTH
    )

    assert response.status_code == 422
    assert "not publicly routable" in response.json()["error"]["message"]


async def test_duplicate_name_is_409(client: httpx.AsyncClient) -> None:
    await _create(client)
    response = await client.post(
        "/api/v1/monitors", json={"name": "api", "url": "https://example.org/"}, headers=AUTH
    )

    assert response.status_code == 409


async def test_invalid_uuid_and_query_bounds(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/monitors/not-a-uuid", headers=AUTH)).status_code == 422
    response = await client.get("/api/v1/monitors", params={"limit": 0}, headers=AUTH)
    assert response.status_code == 422


async def test_check_history_uptime_and_incidents(
    client: httpx.AsyncClient, upstream: Upstream
) -> None:
    created = await _create(client, failure_threshold=1)
    base = f"/api/v1/monitors/{created['id']}"

    ok = await client.post(f"{base}/check", headers=AUTH)
    assert ok.status_code == 200
    assert ok.json()["success"] is True

    upstream.status = 503
    failed = await client.post(f"{base}/check", headers=AUTH)
    assert failed.json()["error"] == "expected status 200, got 503"

    checks = (await client.get(f"{base}/checks", headers=AUTH)).json()
    assert len(checks) == 2

    uptime = (await client.get(f"{base}/uptime", params={"window_hours": 1}, headers=AUTH)).json()
    assert uptime["total_checks"] == 2
    assert uptime["uptime_percent"] == 50.0

    incidents = (await client.get(f"{base}/incidents", headers=AUTH)).json()
    assert len(incidents) == 1
    assert incidents[0]["is_open"] is True

    open_all = await client.get("/api/v1/incidents", params={"open_only": True}, headers=AUTH)
    assert [i["id"] for i in open_all.json()] == [incidents[0]["id"]]

    upstream.status = 200
    await client.post(f"{base}/check", headers=AUTH)
    still_open = await client.get("/api/v1/incidents", params={"open_only": True}, headers=AUTH)
    assert still_open.json() == []


async def test_check_on_missing_monitor_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post(f"/api/v1/monitors/{uuid4()}/check", headers=AUTH)

    assert response.status_code == 404


async def test_request_id_is_echoed_or_generated(client: httpx.AsyncClient) -> None:
    echoed = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    replaced = await client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})
    generated = await client.get("/healthz")

    assert echoed.headers["X-Request-ID"] == "abc-123"
    assert replaced.headers["X-Request-ID"] != "bad id\nwith newline"
    assert len(generated.headers["X-Request-ID"]) == 32


async def test_unexpected_errors_return_500_envelope(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        msg = "kaboom"
        raise RuntimeError(msg)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "internal_error",
        "message": "internal server error",
        "request_id": response.json()["error"]["request_id"],
    }
    assert "kaboom" not in response.text


async def test_docs_hidden_in_production(tmp_path: Path, container: Container) -> None:
    settings = make_settings(tmp_path, environment=Environment.PRODUCTION)
    app = create_app(settings, container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


async def test_docs_can_be_exposed_in_production(tmp_path: Path, container: Container) -> None:
    settings = make_settings(tmp_path, environment=Environment.PRODUCTION, expose_docs=True)
    app = create_app(settings, container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/openapi.json")).status_code == 200


async def test_embedded_worker_runs_for_app_lifetime(
    tmp_path: Path, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    async def fake_run_forever(stop: asyncio.Event) -> None:
        events.append("started")
        await stop.wait()
        events.append("stopped")

    monkeypatch.setattr(container.scheduler, "run_forever", fake_run_forever)
    app = create_app(make_settings(tmp_path, embedded_worker=True), container=container)

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        assert events == ["started"]

    assert events == ["started", "stopped"]
    assert not container.http_client.is_closed


async def test_lifespan_builds_and_closes_own_container(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, api_key=SecretStr(TEST_API_KEY))
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        owned: Container = app.state.container
        assert owned.settings is settings

    assert owned.http_client.is_closed
