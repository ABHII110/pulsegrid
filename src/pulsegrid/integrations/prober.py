"""HTTP prober built on httpx."""

import asyncio
import time

import httpx

from pulsegrid.domain.models import Monitor, ProbeOutcome

MAX_ERROR_LENGTH = 500


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _describe(exc: httpx.HTTPError) -> str:
    detail = str(exc).strip()
    name = type(exc).__name__
    return (f"{name}: {detail}" if detail else name)[:MAX_ERROR_LENGTH]


class HttpxProber:
    """Probes endpoints with a shared ``httpx.AsyncClient``.

    Only response headers are awaited; bodies are never downloaded. Redirects are not followed,
    so a redirect cannot steer the probe to an address the target check would have rejected;
    set ``expected_status`` to 301/302 to monitor a redirecting URL.

    Args:
        client: HTTP client to reuse across probes.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def probe(self, monitor: Monitor) -> ProbeOutcome:
        """Probe the monitor's URL once. Never raises for network or HTTP failures."""
        started = time.perf_counter()
        try:
            async with (
                asyncio.timeout(monitor.timeout_seconds),
                self._client.stream(
                    monitor.method.value,
                    monitor.url,
                    timeout=monitor.timeout_seconds,
                    follow_redirects=False,
                ) as response,
            ):
                status_code = response.status_code
        except (TimeoutError, httpx.TimeoutException):
            return ProbeOutcome(
                success=False,
                status_code=None,
                latency_ms=_elapsed_ms(started),
                error=f"timed out after {monitor.timeout_seconds:g}s",
            )
        except httpx.HTTPError as exc:
            return ProbeOutcome(
                success=False,
                status_code=None,
                latency_ms=_elapsed_ms(started),
                error=_describe(exc),
            )

        latency_ms = _elapsed_ms(started)
        if status_code == monitor.expected_status:
            return ProbeOutcome(
                success=True, status_code=status_code, latency_ms=latency_ms, error=None
            )
        return ProbeOutcome(
            success=False,
            status_code=status_code,
            latency_ms=latency_ms,
            error=f"expected status {monitor.expected_status}, got {status_code}",
        )
