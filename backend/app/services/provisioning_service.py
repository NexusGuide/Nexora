"""Provisioning: a paid order becomes a working VPN account.

    paid order -> subscription -> pick server -> create panel user
               -> fetch configs -> ACTIVE

This is the one flow that spans the database and a third party, so it is the
one most able to leave things half-done. Three rules keep it safe:

1. **Order of operations.** The panel account is created before the
   subscription is marked ACTIVE. If the panel call fails, the customer sees a
   pending subscription and a retry, never an active subscription with nothing
   behind it.
2. **Idempotent at every step.** ``create_user`` on the adapter returns the
   existing account rather than duplicating, and re-running this whole
   function on an already-active subscription does nothing. A worker retry is
   therefore free.
3. **Failures are recorded, not swallowed.** A panel error leaves the
   subscription PENDING with ``provisioning_error`` set, so the job retries and
   an operator can see why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, PanelError
from app.models.billing import Order, Subscription
from app.models.enums import OrderStatus, SubscriptionStatus
from app.models.panel import Panel
from app.panels.manager import PanelManager
from app.services.config_service import ConfigService
from app.services.order_service import plan_from_snapshot
from app.services.panel_service import PanelService
from app.services.subscription_service import (
    SubscriptionService,
    generate_panel_username,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProvisionResult:
    subscription_id: str
    activated: bool
    config_count: int
    already_active: bool = False


class ProvisioningService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        manager: PanelManager | None = None,
    ) -> None:
        self.session = session
        self.manager = manager or PanelManager()
        self.panels = PanelService(session, self.manager)
        self.subscriptions = SubscriptionService(session)
        self.configs = ConfigService(session)

    async def provision_order(self, order_id: str) -> ProvisionResult:
        """Provision the subscription a paid order entitles the user to.

        Safe to call repeatedly: the worker runs it under at-least-once
        delivery, so a duplicate run is expected, not exceptional.
        """
        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
        if order.status is not OrderStatus.PAID:
            raise ConflictError(
                "Only a paid order can be provisioned", code="ORDER_NOT_PAID"
            )

        subscription, _ = await self.subscriptions.create_from_order(order_id)

        if (
            subscription.status is SubscriptionStatus.ACTIVE
            and subscription.panel_username
        ):
            # Already done on an earlier attempt. Refresh the configs, since
            # this run may have been triggered precisely because they were
            # missing, but do not touch the dates.
            count = await self._sync_configs(subscription)
            await self.session.flush()
            return ProvisionResult(
                subscription_id=subscription.id,
                activated=False,
                config_count=count,
                already_active=True,
            )

        terms = plan_from_snapshot(order)
        panel, server = await self.panels.select_server(order.plan_id)

        # Reuse the username from a previous failed attempt, so a retry
        # addresses the same panel account rather than creating another.
        username = subscription.panel_username or generate_panel_username(subscription.id)
        expire_at = datetime.now(UTC) + timedelta(days=int(terms["duration_days"]))

        try:
            adapter = self.manager.adapter_for(panel)
            async with adapter:
                panel_user = await adapter.create_user(
                    username,
                    traffic_limit_bytes=int(terms["traffic_limit_bytes"]),
                    expire_at=expire_at,
                    device_limit=int(terms["device_limit"]),
                    group_ids=panel.group_id_list or None,
                )
                config_uris = list(panel_user.configs)
                if not config_uris:
                    # Some panels do not return links on create.
                    config_uris = await adapter.get_configs(username)
        except PanelError as exc:
            # Record the username even on failure, so the retry reuses it.
            subscription.panel_id = panel.id
            subscription.panel_username = username
            subscription.provisioning_error = exc.message[:500]
            await self.session.flush()
            logger.warning(
                "provisioning_failed",
                extra={
                    "extra_fields": {
                        "subscription_id": subscription.id,
                        "panel_id": panel.id,
                        "code": exc.code,
                    }
                },
            )
            raise

        await self.subscriptions.activate(
            subscription.id,
            panel_id=panel.id,
            panel_username=username,
            subscription_url=panel_user.subscription_url,
            duration_days=int(terms["duration_days"]),
        )
        count = await self._store_configs(subscription, config_uris, server.id)
        await self.session.flush()

        logger.info(
            "provisioning_succeeded",
            extra={
                "extra_fields": {
                    "subscription_id": subscription.id,
                    "panel_id": panel.id,
                    "configs": count,
                }
            },
        )
        return ProvisionResult(
            subscription_id=subscription.id, activated=True, config_count=count
        )

    # ------------------------------------------------------------- refresh
    async def refresh_configs(self, subscription_id: str) -> int:
        """Re-fetch a subscription's configs from its panel (spec rule 9)."""
        subscription = await self.session.get(Subscription, subscription_id)
        if subscription is None:
            raise NotFoundError("Subscription not found", code="SUBSCRIPTION_NOT_FOUND")
        if not subscription.panel_id or not subscription.panel_username:
            raise ConflictError(
                "This subscription has not been provisioned yet",
                code="SUBSCRIPTION_NOT_PROVISIONED",
            )
        return await self._sync_configs(subscription)

    # --------------------------------------------------------- usage sync
    async def sync_usage(self, subscription_id: str) -> int:
        """Pull traffic usage from the panel into the subscription."""
        subscription = await self.session.get(Subscription, subscription_id)
        if subscription is None:
            raise NotFoundError("Subscription not found", code="SUBSCRIPTION_NOT_FOUND")
        if not subscription.panel_id or not subscription.panel_username:
            return 0

        panel = await self.session.get(Panel, subscription.panel_id)
        if panel is None:
            return 0

        adapter = self.manager.adapter_for(panel)
        async with adapter:
            usage = await adapter.get_usage(subscription.panel_username)

        await self.subscriptions.record_usage(subscription.id, usage.used_bytes)
        return usage.used_bytes

    # ------------------------------------------------------------- expiry
    async def disable_on_panel(self, subscription_id: str) -> bool:
        """Disable the panel account behind an expired subscription.

        Called before marking the subscription EXPIRED, because the order
        matters: marking it expired first would leave a working panel account
        that nothing points at.
        """
        subscription = await self.session.get(Subscription, subscription_id)
        if subscription is None or not subscription.panel_id:
            return False
        if not subscription.panel_username:
            return False

        panel = await self.session.get(Panel, subscription.panel_id)
        if panel is None:
            return False

        adapter = self.manager.adapter_for(panel)
        async with adapter:
            await adapter.disable_user(subscription.panel_username)
        return True

    # ---------------------------------------------------------- internals
    async def _sync_configs(self, subscription: Subscription) -> int:
        panel = await self.session.get(Panel, subscription.panel_id)
        if panel is None or not subscription.panel_username:
            return 0

        adapter = self.manager.adapter_for(panel)
        async with adapter:
            uris = await adapter.get_configs(subscription.panel_username)
        return await self._store_configs(subscription, uris, None)

    async def _store_configs(
        self,
        subscription: Subscription,
        uris: list[str],
        server_id: str | None,
    ) -> int:
        stored = await self.configs.replace_for_subscription(
            subscription.id, uris, server_id=server_id
        )
        subscription.last_synced_at = datetime.now(UTC)
        return len(stored)
