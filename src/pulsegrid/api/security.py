"""API key authentication."""

import hmac
from typing import Annotated

from fastapi import Security
from fastapi.security import APIKeyHeader

from pulsegrid.api.deps import ContainerDep
from pulsegrid.core.exceptions import AuthenticationError

API_KEY_HEADER = "X-API-Key"

_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


async def require_api_key(
    container: ContainerDep,
    presented: Annotated[str | None, Security(_api_key_header)],
) -> None:
    """Reject requests without the configured API key (constant-time comparison).

    Raises:
        AuthenticationError: If the key is missing or wrong.
    """
    expected = container.settings.api_key
    if (
        expected is None
        or presented is None
        or not hmac.compare_digest(presented.encode(), expected.get_secret_value().encode())
    ):
        msg = "missing or invalid API key"
        raise AuthenticationError(msg)
