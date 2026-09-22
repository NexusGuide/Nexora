"""Worker process: consume jobs and run their handlers.

Jobs are locked per subject while they run, so two workers cannot provision
the same subscription at once. The lock is an optimisation — the handlers are
idempotent, so a lost lock costs duplicated work, never a duplicated
subscription.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_ctx
from app.workers.jobs import HANDLERS
from app.workers.locks import build_lock
from app.workers.queue import Job, build_queue

logger = logging.getLogger(__name__)

# How long a handler may run before the worker gives up on it. Without this, a
# panel that accepts a connection and then never answers would pin a worker
# permanently.
JOB_TIMEOUT_SECONDS = 120
LOCK_TTL_SECONDS = 180


def lock_key_for(job: Job) -> str | None:
    """The subject a job must not run concurrently with itself on."""
    payload = job.payload
    if subscription_id := payload.get("subscription_id"):
        return f"subscription:{subscription_id}"
    if order_id := payload.get("order_id"):
        return f"order:{order_id}"
    # Sweeps have no single subject; one instance at a time is enough.
    return f"job:{job.name}"


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.queue = build_queue(self.settings.redis_url)
        self.lock = build_lock(self.settings.redis_url)
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        # Finishes the job in flight rather than dropping it: an unacked job
        # would be redelivered, which is safe but wasteful.
        logger.info("worker_stop_requested")
        self._stopping.set()

    async def run(self) -> None:
        logger.info(
            "worker_started",
            extra={"extra_fields": {"handlers": sorted(HANDLERS)}},
        )
        while not self._stopping.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker_loop_error")
                await asyncio.sleep(1)

        await self.queue.close()
        with contextlib.suppress(Exception):
            await self.lock.close()
        logger.info("worker_stopped")

    async def _tick(self) -> None:
        promote = getattr(self.queue, "promote_due_jobs", None)
        if promote is not None:
            await promote()

        reclaim = getattr(self.queue, "reclaim_stale", None)
        jobs = list(await reclaim()) if reclaim is not None else []
        jobs += await self.queue.reserve(block_ms=2000, count=4)

        for job in jobs:
            if self._stopping.is_set():
                break
            await self._run_job(job)

    async def _run_job(self, job: Job) -> None:
        handler = HANDLERS.get(job.name)
        if handler is None:
            # Nothing will ever handle it; retrying forever is pointless.
            logger.error("job_unknown", extra={"extra_fields": {"job": job.name}})
            await self.queue.dead_letter(job, f"No handler for {job.name}")
            return

        token = request_id_ctx.set(job.job_id)
        key = lock_key_for(job)
        try:
            async with self.lock.acquire(key, ttl_seconds=LOCK_TTL_SECONDS) as held:
                if not held:
                    # Another worker has this subject. Ack and move on: that
                    # worker's run covers this one.
                    logger.info(
                        "job_skipped_locked",
                        extra={"extra_fields": {"job": job.name, "key": key}},
                    )
                    await self.queue.ack(job)
                    return

                await asyncio.wait_for(handler(job.payload), timeout=JOB_TIMEOUT_SECONDS)
                await self.queue.ack(job)
                logger.info(
                    "job_done",
                    extra={"extra_fields": {"job": job.name, "attempt": job.attempt}},
                )
        except TimeoutError:
            await self.queue.retry(job, "handler timed out")
        except Exception as exc:
            logger.exception(
                "job_failed",
                extra={"extra_fields": {"job": job.name, "attempt": job.attempt}},
            )
            await self.queue.retry(job, f"{type(exc).__name__}: {exc}")
        finally:
            request_id_ctx.reset(token)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=not settings.app_debug)

    worker = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.request_stop)

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
