"""Lock implementations preventing duplicate concurrent checks."""

import secrets
from collections.abc import Awaitable
from datetime import datetime, timedelta
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from pulsegrid.core.clock import Clock
from pulsegrid.core.exceptions import InfrastructureError

# Delete the key only if it still holds our token, so an expired lock re-acquired by another
# worker is never released by the previous owner.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class InMemoryLock:
    """Process-local lock with expiry, for single-worker deployments and tests.

    Args:
        clock: Time source used for expiry.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._expiry: dict[str, datetime] = {}

    async def acquire(self, key: str, ttl_seconds: int) -> bool:
        """Take ``key`` unless an unexpired holder exists."""
        now = self._clock.now()
        expires = self._expiry.get(key)
        if expires is not None and expires > now:
            return False
        self._expiry[key] = now + timedelta(seconds=ttl_seconds)
        return True

    async def release(self, key: str) -> None:
        """Release ``key``."""
        self._expiry.pop(key, None)


class RedisLock:
    """Redis-backed lock (``SET NX EX`` with an ownership token).

    Args:
        client: Redis client.
    """

    def __init__(self, client: Redis) -> None:
        self._client = client
        self._tokens: dict[str, str] = {}

    async def acquire(self, key: str, ttl_seconds: int) -> bool:
        """Try to take ``key`` for ``ttl_seconds``.

        Raises:
            InfrastructureError: If Redis is unreachable.
        """
        token = secrets.token_hex(16)
        try:
            acquired = await cast(
                Awaitable[bool | None],
                self._client.set(key, token, nx=True, ex=ttl_seconds),
            )
        except RedisError as exc:
            msg = "lock backend unavailable"
            raise InfrastructureError(msg) from exc
        if not acquired:
            return False
        self._tokens[key] = token
        return True

    async def release(self, key: str) -> None:
        """Release ``key`` if this instance still owns it.

        Raises:
            InfrastructureError: If Redis is unreachable.
        """
        token = self._tokens.pop(key, None)
        if token is None:
            return
        try:
            await cast(Awaitable[int], self._client.eval(_RELEASE_SCRIPT, 1, key, token))
        except RedisError as exc:
            msg = "lock backend unavailable"
            raise InfrastructureError(msg) from exc
