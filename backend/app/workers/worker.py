"""Background worker entrypoint (spec rule 22).

Phase 1 ships the process and its wiring so the deployment topology is real.
The job handlers arrive with the subscription and payment flows in phase 2;
until then this process idles rather than pretending to consume a queue.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

# TODO(phase-2): register handlers for create_panel_user, sync_usage,
# sync_subscriptions, expire_subscriptions, send_notifications,
# refresh_configs and process_payment, and consume them from Redis.


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.warning(
        "worker_started_without_handlers",
        extra={"extra_fields": {"note": "job handlers land in phase 2"}},
    )
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
