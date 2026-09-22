"""Scheduler process (spec rule 23).

Enqueues periodic work rather than doing it. The scheduler stays a thin timer:
if it did the work itself, a slow sweep would delay every other schedule, and
scaling would mean running two schedulers, which would double every job.

Each tick takes a short distributed lock, so running two scheduler replicas
for availability does not double-enqueue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.jobs import (
    EXPIRE_SUBSCRIPTIONS,
    SYNC_PANEL_HEALTH,
    SYNC_USAGE,
)
from app.workers.locks import build_lock
from app.workers.queue import build_queue

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Schedule:
    job_name: str
    interval_seconds: int
    payload: dict
    last_run: datetime | None = None

    def is_due(self, now: datetime) -> bool:
        if self.last_run is None:
            return True
        return (now - self.last_run).total_seconds() >= self.interval_seconds


SCHEDULES: list[Schedule] = [
    # Expiry is the one with a customer-visible cost if it runs late, so it is
    # the most frequent.
    Schedule(EXPIRE_SUBSCRIPTIONS, interval_seconds=300, payload={"limit": 200}),
    # Usage sync hits every panel, so it is deliberately less frequent.
    Schedule(SYNC_USAGE, interval_seconds=900, payload={}),
    Schedule(SYNC_PANEL_HEALTH, interval_seconds=600, payload={}),
]

TICK_SECONDS = 30


class Scheduler:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.queue = build_queue(self.settings.redis_url)
        self.lock = build_lock(self.settings.redis_url)
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        logger.info("scheduler_stop_requested")
        self._stopping.set()

    async def run(self) -> None:
        logger.info(
            "scheduler_started",
            extra={
                "extra_fields": {
                    "schedules": {s.job_name: s.interval_seconds for s in SCHEDULES}
                }
            },
        )
        while not self._stopping.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("scheduler_loop_error")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=TICK_SECONDS)

        await self.queue.close()
        with contextlib.suppress(Exception):
            await self.lock.close()
        logger.info("scheduler_stopped")

    async def _tick(self) -> None:
        # Promote jobs whose retry backoff has elapsed.
        promote = getattr(self.queue, "promote_due_jobs", None)
        if promote is not None:
            promoted = await promote()
            if promoted:
                logger.info(
                    "delayed_jobs_promoted",
                    extra={"extra_fields": {"count": promoted}},
                )

        now = datetime.now(UTC)
        for schedule in SCHEDULES:
            if not schedule.is_due(now):
                continue

            # The lock TTL is just under the interval, so a second scheduler
            # cannot enqueue the same job within the same window.
            async with self.lock.acquire(
                f"schedule:{schedule.job_name}",
                ttl_seconds=max(10, schedule.interval_seconds - 5),
            ) as held:
                if not held:
                    schedule.last_run = now
                    continue
                await self.queue.enqueue(schedule.job_name, schedule.payload)
                schedule.last_run = now
                logger.info(
                    "schedule_enqueued",
                    extra={"extra_fields": {"job": schedule.job_name}},
                )


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.app_debug)

    scheduler = Scheduler()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, scheduler.request_stop)

    await scheduler.run()


if __name__ == "__main__":
    asyncio.run(main())
