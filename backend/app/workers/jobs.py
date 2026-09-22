"""Job handlers (spec rule 22).

Every handler is idempotent, because delivery is at-least-once and a retry
must be harmless. Each runs in its own database session and commits or rolls
back as a unit: a job that fails halfway must leave nothing partially applied.

Raising from a handler tells the worker to retry with backoff. Returning
normally acknowledges the job. So a handler raises for a transient fault (the
panel is down) and returns for a permanent one it has already recorded (the
order was cancelled), because retrying the latter forever helps nobody.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.db.base import SessionFactory
from app.services.provisioning_service import ProvisioningService
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]

# Job names, referenced by enqueue sites so a typo fails at import rather than
# silently queueing something no worker handles.
CREATE_PANEL_USER = "create_panel_user"
REFRESH_CONFIGS = "refresh_configs"
SYNC_USAGE = "sync_usage"
EXPIRE_SUBSCRIPTIONS = "expire_subscriptions"
SYNC_PANEL_HEALTH = "sync_panel_health"


async def create_panel_user(payload: dict[str, Any]) -> None:
    """Provision a paid order. The core of the purchase flow."""
    order_id = payload["order_id"]
    async with SessionFactory() as session:
        service = ProvisioningService(session)
        try:
            result = await service.provision_order(order_id)
            await session.commit()
        except ConflictError as exc:
            # A permanent condition: the order is not payable or no server is
            # free. NO_SERVER_AVAILABLE is worth retrying; the rest are not.
            await session.rollback()
            if exc.code == "NO_SERVER_AVAILABLE":
                raise
            logger.warning(
                "provisioning_skipped",
                extra={"extra_fields": {"order_id": order_id, "code": exc.code}},
            )
            return
        except NotFoundError:
            await session.rollback()
            logger.warning(
                "provisioning_order_missing",
                extra={"extra_fields": {"order_id": order_id}},
            )
            return
        except AppError:
            # Panel errors and the like: transient, so let the worker retry.
            await session.rollback()
            raise

    logger.info(
        "job_create_panel_user_done",
        extra={
            "extra_fields": {
                "order_id": order_id,
                "subscription_id": result.subscription_id,
                "configs": result.config_count,
            }
        },
    )


async def refresh_configs(payload: dict[str, Any]) -> None:
    subscription_id = payload["subscription_id"]
    async with SessionFactory() as session:
        service = ProvisioningService(session)
        try:
            count = await service.refresh_configs(subscription_id)
            await session.commit()
        except (ConflictError, NotFoundError):
            await session.rollback()
            return
        except AppError:
            await session.rollback()
            raise
    logger.info(
        "job_refresh_configs_done",
        extra={"extra_fields": {"subscription_id": subscription_id, "configs": count}},
    )


async def sync_usage(payload: dict[str, Any]) -> None:
    """Pull traffic for one subscription, or for all active ones."""
    subscription_id = payload.get("subscription_id")

    async with SessionFactory() as session:
        service = ProvisioningService(session)

        if subscription_id:
            try:
                await service.sync_usage(subscription_id)
                await session.commit()
            except (NotFoundError, ConflictError):
                await session.rollback()
            return

        subscriptions = await SubscriptionService(session).list_active_provisioned()
        synced = 0
        for subscription in subscriptions:
            try:
                await service.sync_usage(subscription.id)
                synced += 1
            except AppError as exc:
                # One unreachable panel must not stop the sweep.
                logger.warning(
                    "usage_sync_failed",
                    extra={
                        "extra_fields": {
                            "subscription_id": subscription.id,
                            "code": getattr(exc, "code", "UNKNOWN"),
                        }
                    },
                )
        await session.commit()
    logger.info("job_sync_usage_done", extra={"extra_fields": {"synced": synced}})


async def expire_subscriptions(payload: dict[str, Any]) -> None:
    """Disable and expire subscriptions past their date (spec rule 18)."""
    async with SessionFactory() as session:
        subscriptions = SubscriptionService(session)
        provisioning = ProvisioningService(session)

        due = await subscriptions.find_expired(limit=int(payload.get("limit", 100)))
        expired = 0
        for subscription in due:
            try:
                # Disable on the panel first: marking it expired first would
                # leave a working panel account that nothing points at.
                await provisioning.disable_on_panel(subscription.id)
            except AppError as exc:
                logger.warning(
                    "expire_disable_failed",
                    extra={
                        "extra_fields": {
                            "subscription_id": subscription.id,
                            "code": getattr(exc, "code", "UNKNOWN"),
                        }
                    },
                )
                # Leave it ACTIVE so the next sweep tries again, rather than
                # marking it expired while the panel account still works.
                continue
            await subscriptions.expire(subscription.id)
            expired += 1
        await session.commit()

    logger.info(
        "job_expire_subscriptions_done",
        extra={"extra_fields": {"found": len(due), "expired": expired}},
    )


async def sync_panel_health(payload: dict[str, Any]) -> None:
    from app.services.panel_service import PanelService

    async with SessionFactory() as session:
        results = await PanelService(session).check_all()
        await session.commit()
    unhealthy = [name for name, ok in results.items() if not ok]
    logger.info(
        "job_sync_panel_health_done",
        extra={"extra_fields": {"checked": len(results), "unhealthy": len(unhealthy)}},
    )


HANDLERS: dict[str, Handler] = {
    CREATE_PANEL_USER: create_panel_user,
    REFRESH_CONFIGS: refresh_configs,
    SYNC_USAGE: sync_usage,
    EXPIRE_SUBSCRIPTIONS: expire_subscriptions,
    SYNC_PANEL_HEALTH: sync_panel_health,
}
