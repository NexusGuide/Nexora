"""Per-endpoint rate limiting (spec rule 37).

nginx already limits at the edge, and login has its own lockout. This adds the
layer between them: a limit the application controls, keyed on the
authenticated user where there is one.

**It fails open.** If Redis is unavailable the request is allowed, with a
warning logged. A rate limiter that takes the whole API down when its cache
dies has caused a worse outage than the abuse it prevents.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.core.config import get_settings
from app.core.exceptions import RateLimitedError

logger = logging.getLogger(__name__)

# Sliding window over a sorted set: timestamps are added, old ones trimmed,
# and the remainder counted. More accurate than a fixed window, which lets a
# caller send 2x the limit across a window boundary.
_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    return {1, count}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, math.ceil(window))
return {0, count + 1}
"""


@dataclass(frozen=True, slots=True)
class Rule:
    limit: int
    window_seconds: int
    name: str


# Auth routes are where credential stuffing lands, so they get a much tighter
# rule than ordinary reads.
AUTH_RULE = Rule(limit=10, window_seconds=60, name="auth")
WRITE_RULE = Rule(limit=30, window_seconds=60, name="write")
DEFAULT_RULE = Rule(limit=120, window_seconds=60, name="default")


class RateLimiter:
    def __init__(self, redis_url: str | None) -> None:
        self._url = redis_url
        self._client: Any = None
        self._sha: str | None = None
        self._unavailable_logged = False

    async def _redis(self) -> Any:
        if not self._url:
            return None
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self._url, decode_responses=True)
            self._sha = await self._client.script_load(_SCRIPT)
        return self._client

    async def check(self, key: str, rule: Rule) -> tuple[bool, int]:
        """Return ``(allowed, current_count)``. Allows on any failure."""
        try:
            client = await self._redis()
            if client is None:
                return True, 0

            now = time.time()
            blocked, count = await client.evalsha(
                self._sha,
                1,
                f"nexus:rl:{rule.name}:{key}",
                str(now),
                str(rule.window_seconds),
                str(rule.limit),
                f"{now}:{id(self)}",
            )
            return not bool(blocked), int(count)
        except Exception:
            if not self._unavailable_logged:
                logger.warning(
                    "rate_limiter_unavailable_failing_open",
                    extra={"extra_fields": {"rule": rule.name}},
                )
                self._unavailable_logged = True
            return True, 0

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._sha = None


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_settings().redis_url)
    return _limiter


async def close_limiter() -> None:
    global _limiter
    if _limiter is not None:
        await _limiter.close()
        _limiter = None


def _client_key(request: Request) -> str:
    """Key on the authenticated user when known, otherwise the client IP.

    Keying only on IP would let one abusive account hide behind a shared
    carrier NAT, and would throttle innocent users behind the same one.
    """
    if user_id := getattr(request.state, "user_id", None):
        return f"u:{user_id}"

    forwarded = request.headers.get("X-Forwarded-For")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )
    return f"ip:{ip}"


def rule_for(request: Request) -> Rule:
    path = request.url.path
    if "/auth/" in path:
        return AUTH_RULE
    if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        return WRITE_RULE
    return DEFAULT_RULE


async def enforce(request: Request) -> None:
    """Raise :class:`RateLimitedError` when the caller is over its limit."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return

    # Health checks come from orchestrators on a schedule; limiting them would
    # make the platform look unhealthy under load, which is exactly backwards.
    if request.url.path.startswith("/health"):
        return

    rule = rule_for(request)
    allowed, _count = await get_limiter().check(_client_key(request), rule)
    if not allowed:
        logger.info(
            "rate_limited",
            extra={
                "extra_fields": {
                    "rule": rule.name,
                    "path": request.url.path,
                }
            },
        )
        raise RateLimitedError(
            f"Too many requests. Try again in up to {rule.window_seconds} seconds.",
            details={"limit": rule.limit, "window_seconds": rule.window_seconds},
        )
