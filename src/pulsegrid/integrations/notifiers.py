"""Incident notification channels."""

import hashlib
import hmac
import json

import httpx

from pulsegrid.core.exceptions import NotificationError
from pulsegrid.core.logging import get_logger
from pulsegrid.domain.models import IncidentEvent
from pulsegrid.domain.schemas import IncidentRead

SIGNATURE_HEADER = "X-PulseGrid-Signature"

logger = get_logger(__name__)


def build_payload(event: IncidentEvent) -> dict[str, object]:
    """Serialise an incident event into the documented webhook payload."""
    return {
        "event": event.type.value,
        "incident": IncidentRead.model_validate(event.incident).model_dump(mode="json"),
        "monitor": {
            "id": str(event.monitor.id),
            "name": event.monitor.name,
            "url": event.monitor.url,
            "status": event.monitor.status.value,
        },
    }


def sign(body: bytes, secret: str) -> str:
    """Return the signature header value for ``body``."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookNotifier:
    """POSTs incident events as JSON, optionally HMAC-signed.

    Args:
        client: HTTP client used for delivery.
        url: Destination URL.
        secret: Shared secret for the ``X-PulseGrid-Signature`` header; unsigned when ``None``.
        timeout_seconds: Per-request timeout.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        secret: str | None,
        timeout_seconds: float,
    ) -> None:
        self._client = client
        self._url = url
        self._secret = secret
        self._timeout = timeout_seconds

    async def notify(self, event: IncidentEvent) -> None:
        """Deliver the event.

        Raises:
            NotificationError: On transport errors or non-2xx responses.
        """
        body = json.dumps(build_payload(event), separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
        if self._secret is not None:
            headers[SIGNATURE_HEADER] = sign(body, self._secret)
        try:
            response = await self._client.post(
                self._url,
                content=body,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            msg = f"webhook delivery failed: {type(exc).__name__}"
            raise NotificationError(msg) from exc
        if not response.is_success:
            msg = f"webhook returned HTTP {response.status_code}"
            raise NotificationError(msg)


class LoggingNotifier:
    """Fallback channel that records incident events in the structured log."""

    async def notify(self, event: IncidentEvent) -> None:
        """Log the event at warning level."""
        logger.warning(
            "incident_event",
            event_type=event.type.value,
            monitor_id=str(event.monitor.id),
            monitor_name=event.monitor.name,
            incident_id=str(event.incident.id),
            cause=event.incident.cause,
        )
