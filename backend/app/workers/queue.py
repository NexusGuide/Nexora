"""Job queue (spec rule 22).

Backed by a Redis Stream with a consumer group, which gives at-least-once
delivery: a job stays pending until the worker acknowledges it, so a worker
that crashes mid-job does not lose it.

At-least-once means a job can run twice. That is safe here *because* every
handler is idempotent — the property phase 2 built on database constraints.
Exactly-once delivery does not exist in a distributed system; idempotent
handlers plus at-least-once delivery is the achievable equivalent.

``InMemoryQueue`` implements the same protocol so tests, and a single-process
development run, need no Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

STREAM_KEY = "nexus:jobs"
GROUP_NAME = "nexus-workers"
DEAD_LETTER_KEY = "nexus:jobs:dead"

DEFAULT_MAX_ATTEMPTS = 5
# Retry delays in seconds. A panel that is down usually stays down for a
# while, so the gap widens rather than hammering it.
BACKOFF_SECONDS = (5, 30, 120, 600)


@dataclass(slots=True)
class Job:
    name: str
    payload: dict[str, Any]
    attempt: int = 1
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    enqueued_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    # Set by the queue implementation; used to acknowledge the message.
    delivery_id: str | None = None

    def to_fields(self) -> dict[str, str]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "attempt": str(self.attempt),
            "enqueued_at": self.enqueued_at,
            "payload": json.dumps(self.payload, ensure_ascii=False),
        }

    @classmethod
    def from_fields(cls, fields: dict[str, Any], delivery_id: str | None = None) -> Job:
        def get(key: str, default: str = "") -> str:
            value = fields.get(key, fields.get(key.encode(), default))
            return value.decode() if isinstance(value, bytes) else str(value)

        return cls(
            name=get("name"),
            payload=json.loads(get("payload", "{}")),
            attempt=int(get("attempt", "1")),
            job_id=get("job_id"),
            enqueued_at=get("enqueued_at"),
            delivery_id=delivery_id,
        )

    def next_delay_seconds(self) -> int:
        index = min(self.attempt - 1, len(BACKOFF_SECONDS) - 1)
        return BACKOFF_SECONDS[index]


class JobQueue(Protocol):
    """What a worker needs from a queue, whatever backs it."""

    async def enqueue(
        self, name: str, payload: dict[str, Any], *, delay_seconds: int = 0
    ) -> str: ...

    async def reserve(self, *, block_ms: int = 5000, count: int = 1) -> list[Job]: ...

    async def ack(self, job: Job) -> None: ...

    async def retry(self, job: Job, error: str) -> None: ...

    async def dead_letter(self, job: Job, error: str) -> None: ...

    async def close(self) -> None: ...


class RedisQueue:
    """Redis Streams implementation."""

    def __init__(
        self,
        redis_url: str,
        *,
        consumer_name: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._url = redis_url
        self._client: Any = None
        self.consumer_name = consumer_name or f"worker-{uuid.uuid4().hex[:8]}"
        self.max_attempts = max_attempts

    async def _redis(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self._url, decode_responses=True)
            # Creating the group is idempotent apart from the BUSYGROUP error,
            # which simply means another worker got here first.
            try:
                await self._client.xgroup_create(
                    STREAM_KEY, GROUP_NAME, id="0", mkstream=True
                )
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
        return self._client

    async def enqueue(
        self, name: str, payload: dict[str, Any], *, delay_seconds: int = 0
    ) -> str:
        job = Job(name=name, payload=payload)
        client = await self._redis()

        if delay_seconds > 0:
            # Delayed jobs live in a sorted set keyed by due time; the
            # scheduler promotes them when they come due.
            due = datetime.now(UTC).timestamp() + delay_seconds
            await client.zadd("nexus:jobs:delayed", {json.dumps(job.to_fields()): due})
            return job.job_id

        await client.xadd(STREAM_KEY, job.to_fields())
        logger.info(
            "job_enqueued",
            extra={"extra_fields": {"job": name, "job_id": job.job_id}},
        )
        return job.job_id

    async def promote_due_jobs(self) -> int:
        """Move delayed jobs whose time has come onto the stream."""
        client = await self._redis()
        now = datetime.now(UTC).timestamp()
        due = await client.zrangebyscore("nexus:jobs:delayed", 0, now, start=0, num=100)
        promoted = 0
        for raw in due:
            # Only the worker that removes the member promotes it, so two
            # schedulers cannot both enqueue the same job.
            if await client.zrem("nexus:jobs:delayed", raw):
                await client.xadd(STREAM_KEY, json.loads(raw))
                promoted += 1
        return promoted

    async def reserve(self, *, block_ms: int = 5000, count: int = 1) -> list[Job]:
        client = await self._redis()
        response = await client.xreadgroup(
            GROUP_NAME,
            self.consumer_name,
            {STREAM_KEY: ">"},
            count=count,
            block=block_ms,
        )
        jobs: list[Job] = []
        for _stream, entries in response or []:
            for delivery_id, fields in entries:
                jobs.append(Job.from_fields(fields, delivery_id=delivery_id))
        return jobs

    async def reclaim_stale(self, *, idle_ms: int = 300_000) -> list[Job]:
        """Take over jobs a dead worker left pending.

        Without this, a worker that is killed mid-job leaves its message
        pending forever and the job silently never completes.
        """
        client = await self._redis()
        _, entries, _ = await client.xautoclaim(
            STREAM_KEY,
            GROUP_NAME,
            self.consumer_name,
            min_idle_time=idle_ms,
            start_id="0-0",
            count=10,
        )
        return [
            Job.from_fields(fields, delivery_id=delivery_id)
            for delivery_id, fields in entries
        ]

    async def ack(self, job: Job) -> None:
        if job.delivery_id is None:
            return
        client = await self._redis()
        await client.xack(STREAM_KEY, GROUP_NAME, job.delivery_id)
        await client.xdel(STREAM_KEY, job.delivery_id)

    async def retry(self, job: Job, error: str) -> None:
        await self.ack(job)
        if job.attempt >= self.max_attempts:
            await self.dead_letter(job, error)
            return

        retried = Job(
            name=job.name,
            payload=job.payload,
            attempt=job.attempt + 1,
            job_id=job.job_id,
        )
        client = await self._redis()
        due = datetime.now(UTC).timestamp() + job.next_delay_seconds()
        await client.zadd("nexus:jobs:delayed", {json.dumps(retried.to_fields()): due})
        logger.warning(
            "job_retry_scheduled",
            extra={
                "extra_fields": {
                    "job": job.name,
                    "job_id": job.job_id,
                    "attempt": retried.attempt,
                    "delay_s": job.next_delay_seconds(),
                }
            },
        )

    async def dead_letter(self, job: Job, error: str) -> None:
        await self.ack(job)
        client = await self._redis()
        await client.xadd(
            DEAD_LETTER_KEY,
            {
                **job.to_fields(),
                "error": error[:500],
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )
        logger.error(
            "job_dead_lettered",
            extra={
                "extra_fields": {
                    "job": job.name,
                    "job_id": job.job_id,
                    "attempts": job.attempt,
                }
            },
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class InMemoryQueue:
    """Same protocol, no Redis. For tests and single-process development.

    State is per-process, so it is not a substitute for Redis in a deployment
    that runs more than one worker.
    """

    def __init__(self, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        self._queue: deque[Job] = deque()
        self._delayed: list[tuple[float, Job]] = []
        self.dead: list[tuple[Job, str]] = []
        self.max_attempts = max_attempts

    async def enqueue(
        self, name: str, payload: dict[str, Any], *, delay_seconds: int = 0
    ) -> str:
        job = Job(name=name, payload=payload)
        if delay_seconds > 0:
            due = datetime.now(UTC).timestamp() + delay_seconds
            self._delayed.append((due, job))
        else:
            self._queue.append(job)
        return job.job_id

    async def promote_due_jobs(self) -> int:
        now = datetime.now(UTC).timestamp()
        due = [(t, j) for t, j in self._delayed if t <= now]
        self._delayed = [(t, j) for t, j in self._delayed if t > now]
        for _, job in due:
            self._queue.append(job)
        return len(due)

    async def reserve(self, *, block_ms: int = 5000, count: int = 1) -> list[Job]:
        jobs: list[Job] = []
        while self._queue and len(jobs) < count:
            jobs.append(self._queue.popleft())
        if not jobs and block_ms:
            await asyncio.sleep(min(block_ms, 50) / 1000)
        return jobs

    async def reclaim_stale(self, *, idle_ms: int = 300_000) -> list[Job]:
        return []

    async def ack(self, job: Job) -> None:
        return None

    async def retry(self, job: Job, error: str) -> None:
        if job.attempt >= self.max_attempts:
            await self.dead_letter(job, error)
            return
        self._queue.append(
            Job(
                name=job.name,
                payload=job.payload,
                attempt=job.attempt + 1,
                job_id=job.job_id,
            )
        )

    async def dead_letter(self, job: Job, error: str) -> None:
        self.dead.append((job, error))

    async def close(self) -> None:
        return None

    @property
    def depth(self) -> int:
        return len(self._queue)


def build_queue(redis_url: str | None) -> JobQueue:
    """Redis when configured, in-memory otherwise."""
    if redis_url:
        return RedisQueue(redis_url)
    return InMemoryQueue()
