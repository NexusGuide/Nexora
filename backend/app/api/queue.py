"""The API's handle on the job queue.

One queue instance per process, created lazily and closed on shutdown. The API
only ever *enqueues*; the worker owns execution. Keeping that split means a
slow panel can never make an HTTP request slow.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.workers.queue import JobQueue, build_queue

logger = logging.getLogger(__name__)

_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    global _queue
    if _queue is None:
        _queue = build_queue(get_settings().redis_url)
    return _queue


async def close_queue() -> None:
    global _queue
    if _queue is not None:
        await _queue.close()
        _queue = None


async def enqueue(name: str, payload: dict[str, Any]) -> str | None:
    """Enqueue a job, without letting a queue outage fail the request.

    The caller has already committed its database work. If Redis is down, the
    job is lost, but the order is not — and the scheduler's periodic sweeps
    pick up unprovisioned paid orders. Failing the HTTP request instead would
    tell the customer their purchase failed when it did not.
    """
    try:
        return await get_queue().enqueue(name, payload)
    except Exception:
        logger.exception(
            "enqueue_failed", extra={"extra_fields": {"job": name, **payload}}
        )
        return None
