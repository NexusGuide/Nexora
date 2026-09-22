"""Distributed locks (spec rule 24).

Used to serialise operations on one subscription across workers: provisioning
and a usage sync must not run against the same panel account at once, or they
race on the subscription row.

The lock is advisory and time-bounded. It reduces contention; it is not what
makes the system correct. Correctness comes from the idempotent handlers and
the database constraints underneath — a lock that expires mid-operation must
not be able to corrupt anything, and here it cannot.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Releases only if the value still matches, so a lock that expired and was
# taken by somebody else is never released by the previous holder.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class Lock(Protocol):
    def acquire(
        self, key: str, *, ttl_seconds: int = 60, wait_seconds: float = 0
    ) -> AsyncIterator[bool]: ...


class RedisLock:
    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client: Any = None

    async def _redis(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self._url, decode_responses=True)
        return self._client

    @contextlib.asynccontextmanager
    async def acquire(
        self, key: str, *, ttl_seconds: int = 60, wait_seconds: float = 0
    ) -> AsyncIterator[bool]:
        """Hold ``key`` for the block, yielding whether it was acquired.

        The caller decides what a failed acquisition means — usually "somebody
        else is already doing this, so do nothing".
        """
        client = await self._redis()
        full_key = f"nexus:lock:{key}"
        token = secrets.token_hex(16)
        deadline = asyncio.get_event_loop().time() + wait_seconds
        acquired = False

        while True:
            acquired = bool(await client.set(full_key, token, nx=True, ex=ttl_seconds))
            if acquired or asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.1)

        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await client.eval(_RELEASE_SCRIPT, 1, full_key, token)
                except Exception:
                    logger.warning(
                        "lock_release_failed", extra={"extra_fields": {"key": key}}
                    )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class InMemoryLock:
    """Process-local lock for tests and single-process runs."""

    def __init__(self) -> None:
        self._held: set[str] = set()

    @contextlib.asynccontextmanager
    async def acquire(
        self, key: str, *, ttl_seconds: int = 60, wait_seconds: float = 0
    ) -> AsyncIterator[bool]:
        acquired = key not in self._held
        if acquired:
            self._held.add(key)
        try:
            yield acquired
        finally:
            if acquired:
                self._held.discard(key)

    async def close(self) -> None:
        self._held.clear()


def build_lock(redis_url: str | None) -> RedisLock | InMemoryLock:
    if redis_url:
        return RedisLock(redis_url)
    return InMemoryLock()
