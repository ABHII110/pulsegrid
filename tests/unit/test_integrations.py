import asyncio
import hashlib
import hmac
import json
from collections.abc import Callable, Coroutine
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from pulsegrid.core.exceptions import InfrastructureError, NotificationError
from pulsegrid.core.logging import configure_logging
from pulsegrid.domain.models import (
    HttpMethod,
    Incident,
    IncidentEvent,
    IncidentEventType,
    MonitorStatus,
)
from pulsegrid.integrations.locks import InMemoryLock, RedisLock
from pulsegrid.integrations.notifiers import (
    SIGNATURE_HEADER,
    LoggingNotifier,
    WebhookNotifier,
    build_payload,
    sign,
)
from pulsegrid.integrations.prober import HttpxProber
from tests.fakes import T0, FakeClock, make_monitor

Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- HttpxProber ------------------------------------------------------------------------------


async def test_probe_success_uses_method_and_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    monitor = make_monitor(method=HttpMethod.HEAD, expected_status=204)
    async with _client(handler) as client:
        outcome = await HttpxProber(client).probe(monitor)

    assert outcome.success
    assert outcome.status_code == 204
    assert outcome.error is None
    assert outcome.latency_ms >= 0
    assert seen[0].method == "HEAD"
    assert str(seen[0].url) == monitor.url


async def test_probe_unexpected_status_fails() -> None:
    async with _client(lambda _: httpx.Response(500)) as client:
        outcome = await HttpxProber(client).probe(make_monitor())

    assert not outcome.success
    assert outcome.status_code == 500
    assert outcome.error == "expected status 200, got 500"


async def test_probe_does_not_follow_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    async with _client(handler) as client:
        outcome = await HttpxProber(client).probe(make_monitor())

    assert outcome.status_code == 302
    assert not outcome.success


async def test_probe_transport_error_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _client(handler) as client:
        outcome = await HttpxProber(client).probe(make_monitor())

    assert not outcome.success
    assert outcome.status_code is None
    assert outcome.error == "ConnectError: connection refused"


async def test_probe_error_without_message_uses_type_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("", request=request)

    async with _client(handler) as client:
        outcome = await HttpxProber(client).probe(make_monitor())

    assert outcome.error == "RemoteProtocolError"


async def test_probe_httpx_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with _client(handler) as client:
        outcome = await HttpxProber(client).probe(make_monitor(timeout_seconds=2.5))

    assert outcome.error == "timed out after 2.5s"


async def test_probe_overall_deadline() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    async with _client(handler) as client:
        outcome = await HttpxProber(client).probe(make_monitor(timeout_seconds=0.05))

    assert not outcome.success
    assert outcome.error == "timed out after 0.05s"


# --- Notifiers --------------------------------------------------------------------------------


def _event() -> IncidentEvent:
    monitor = make_monitor(name="api", status=MonitorStatus.DOWN)
    incident = Incident(
        id=uuid4(), monitor_id=monitor.id, opened_at=T0, resolved_at=None, cause="HTTP 503"
    )
    return IncidentEvent(type=IncidentEventType.OPENED, incident=incident, monitor=monitor)


def test_payload_shape() -> None:
    event = _event()

    payload = build_payload(event)

    assert payload["event"] == "incident.opened"
    incident = cast(dict[str, Any], payload["incident"])
    assert incident["id"] == str(event.incident.id)
    assert incident["is_open"] is True
    assert payload["monitor"] == {
        "id": str(event.monitor.id),
        "name": "api",
        "url": event.monitor.url,
        "status": "down",
    }


async def test_webhook_posts_signed_json() -> None:
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200)

    event = _event()
    async with _client(handler) as client:
        notifier = WebhookNotifier(
            client, "https://hooks.example.com/x", secret="s3cret", timeout_seconds=3
        )
        await notifier.notify(event)

    request = received[0]
    assert request.method == "POST"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers[SIGNATURE_HEADER] == sign(request.content, "s3cret")
    assert json.loads(request.content) == build_payload(event)


async def test_webhook_without_secret_is_unsigned() -> None:
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(202)

    async with _client(handler) as client:
        await WebhookNotifier(client, "https://h.example/", secret=None, timeout_seconds=1).notify(
            _event()
        )

    assert SIGNATURE_HEADER not in received[0].headers


async def test_webhook_error_status_raises() -> None:
    async with _client(lambda _: httpx.Response(500)) as client:
        notifier = WebhookNotifier(client, "https://h.example/", secret=None, timeout_seconds=1)
        with pytest.raises(NotificationError, match="HTTP 500"):
            await notifier.notify(_event())


async def test_webhook_transport_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with _client(handler) as client:
        notifier = WebhookNotifier(client, "https://h.example/", secret=None, timeout_seconds=1)
        with pytest.raises(NotificationError, match="ConnectError"):
            await notifier.notify(_event())


async def test_logging_notifier_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json_output=True)
    await LoggingNotifier().notify(_event())

    assert "incident_event" in capsys.readouterr().err


def test_signature_is_hmac_sha256() -> None:
    expected = hmac.new(b"k", b"{}", hashlib.sha256).hexdigest()

    assert sign(b"{}", "k") == f"sha256={expected}"
    assert sign(b"{}", "k") != sign(b"{}", "other")


# --- Locks ------------------------------------------------------------------------------------


async def test_in_memory_lock_expires() -> None:
    clock = FakeClock()
    lock = InMemoryLock(clock)

    assert await lock.acquire("k", 10)
    assert not await lock.acquire("k", 10)
    clock.advance(seconds=11)
    assert await lock.acquire("k", 10)
    await lock.release("k")
    assert await lock.acquire("k", 10)


class FakeRedis:
    def __init__(self, *, broken: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.broken = broken

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool | None:
        if self.broken:
            msg = "redis down"
            raise RedisConnectionError(msg)
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.ttls[key] = ex
        return True

    async def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        if self.broken:
            msg = "redis down"
            raise RedisConnectionError(msg)
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


async def test_redis_lock_acquire_and_release() -> None:
    fake = FakeRedis()
    first, second = RedisLock(cast(Redis, fake)), RedisLock(cast(Redis, fake))

    assert await first.acquire("k", 40)
    assert fake.ttls["k"] == 40
    assert not await second.acquire("k", 40)
    await second.release("k")
    assert "k" in fake.store
    await first.release("k")
    assert "k" not in fake.store


async def test_redis_lock_does_not_release_foreign_token() -> None:
    fake = FakeRedis()
    lock = RedisLock(cast(Redis, fake))
    assert await lock.acquire("k", 1)
    fake.store["k"] = "someone-else"

    await lock.release("k")

    assert fake.store["k"] == "someone-else"


async def test_redis_errors_become_infrastructure_errors() -> None:
    fake = FakeRedis()
    lock = RedisLock(cast(Redis, fake))
    assert await lock.acquire("k", 1)
    fake.broken = True

    with pytest.raises(InfrastructureError):
        await lock.acquire("other", 1)
    with pytest.raises(InfrastructureError):
        await lock.release("k")
