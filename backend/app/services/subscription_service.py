"""Subscription lifecycle (spec rules 17-18).

    PENDING -> ACTIVE -> EXPIRED
    ACTIVE  -> SUSPENDED -> ACTIVE

Provisioning against a panel is deliberately *not* done here. It is slow,
involves a third party that can be down, and must be retryable — so it belongs
in a worker job. This service owns the database side of the lifecycle and
exposes the operations that job calls.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.models.billing import Order, Subscription
from app.models.enums import OrderStatus, SubscriptionStatus
from app.services.order_service import plan_from_snapshot

logger = logging.getLogger(__name__)


def generate_panel_username(subscription_id: str) -> str:
    """A panel-side username that is unique and reveals nothing about the user.

    Deriving it from the subscription id rather than the person's username
    keeps customer identity out of the panel, which is shared infrastructure
    and often has its own operators.
    """
    return f"nx_{subscription_id.replace('-', '')[:16]}{secrets.token_hex(3)}"


class SubscriptionService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ------------------------------------------------------------ create
    async def create_from_order(self, order_id: str) -> tuple[Subscription, bool]:
        """Create the subscription a paid order entitles the user to.

        Returns ``(subscription, created)``. Idempotent by design: this runs in
        a worker job that may be retried after a crash, and a second run must
        not hand the customer a second subscription.
        """
        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
        if order.status is not OrderStatus.PAID:
            raise ConflictError(
                "Only a paid order can be provisioned",
                code="ORDER_NOT_PAID",
            )

        # A renewal extends the existing subscription instead of creating one.
        if order.subscription_id:
            subscription = await self.session.get(Subscription, order.subscription_id)
            if subscription is not None:
                await self.renew(subscription.id, order_id=order.id)
                return subscription, False

        existing = await self.session.scalar(
            select(Subscription)
            .join(Order, Order.subscription_id == Subscription.id)
            .where(Order.id == order.id)
        )
        if existing is not None:
            return existing, False

        terms = plan_from_snapshot(order)
        subscription = Subscription(
            user_id=order.user_id,
            plan_id=order.plan_id,
            traffic_limit_bytes=int(terms["traffic_limit_bytes"]),
            device_limit=int(terms["device_limit"]),
            status=SubscriptionStatus.PENDING,
        )
        self.session.add(subscription)
        await self.session.flush()

        # Link the order back, so a retry of this job finds the subscription.
        order.subscription_id = subscription.id
        await self.session.flush()

        logger.info(
            "subscription_created",
            extra={
                "extra_fields": {
                    "subscription_id": subscription.id,
                    "order_id": order.id,
                }
            },
        )
        return subscription, True

    # ---------------------------------------------------------- activate
    async def activate(
        self,
        subscription_id: str,
        *,
        panel_id: str,
        panel_username: str,
        subscription_url: str | None = None,
        duration_days: int | None = None,
    ) -> Subscription:
        """Mark a subscription live after the panel account exists."""
        subscription = await self._get(subscription_id)

        if subscription.status is SubscriptionStatus.ACTIVE:
            return subscription

        now = datetime.now(UTC)
        days = duration_days
        if days is None:
            order = await self.session.scalar(
                select(Order).where(Order.subscription_id == subscription.id)
            )
            days = int(plan_from_snapshot(order)["duration_days"]) if order else 30

        subscription.panel_id = panel_id
        subscription.panel_username = panel_username
        subscription.subscription_url = subscription_url
        subscription.start_at = now
        subscription.expire_at = now + timedelta(days=days)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.provisioning_error = None
        await self.session.flush()

        logger.info(
            "subscription_activated",
            extra={"extra_fields": {"subscription_id": subscription.id}},
        )
        return subscription

    # ------------------------------------------------------------- renew
    async def renew(
        self,
        subscription_id: str,
        *,
        order_id: str,
        reset_traffic: bool = True,
    ) -> Subscription:
        """Extend a subscription by the ordered plan's duration.

        An expired subscription restarts from now; a still-active one is
        extended from its current expiry, so renewing early never costs the
        customer the days they already paid for.
        """
        subscription = await self._get(subscription_id)
        order = await self.session.get(Order, order_id)
        if order is None or order.status is not OrderStatus.PAID:
            raise ConflictError(
                "Only a paid order can renew a subscription",
                code="ORDER_NOT_PAID",
            )

        terms = plan_from_snapshot(order)
        now = datetime.now(UTC)

        base = now
        if subscription.expire_at is not None:
            current = subscription.expire_at
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            if current > now:
                base = current

        subscription.expire_at = base + timedelta(days=int(terms["duration_days"]))
        subscription.traffic_limit_bytes = int(terms["traffic_limit_bytes"])
        subscription.device_limit = int(terms["device_limit"])
        if reset_traffic:
            subscription.traffic_used_bytes = 0
        if subscription.status in {
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.SUSPENDED,
        }:
            subscription.status = SubscriptionStatus.ACTIVE

        await self.session.flush()
        logger.info(
            "subscription_renewed",
            extra={"extra_fields": {"subscription_id": subscription.id}},
        )
        return subscription

    # --------------------------------------------------- state changes
    async def suspend(self, subscription_id: str, reason: str) -> Subscription:
        subscription = await self._get(subscription_id)
        if subscription.status is not SubscriptionStatus.ACTIVE:
            raise ConflictError(
                "Only an active subscription can be suspended",
                code="SUBSCRIPTION_NOT_ACTIVE",
            )
        subscription.status = SubscriptionStatus.SUSPENDED
        subscription.provisioning_error = reason[:500]
        await self.session.flush()
        return subscription

    async def resume(self, subscription_id: str) -> Subscription:
        subscription = await self._get(subscription_id)
        if subscription.status is not SubscriptionStatus.SUSPENDED:
            raise ConflictError(
                "Only a suspended subscription can be resumed",
                code="SUBSCRIPTION_NOT_SUSPENDED",
            )
        if self._is_past_expiry(subscription):
            raise ConflictError(
                "This subscription has expired and must be renewed instead",
                code="SUBSCRIPTION_EXPIRED",
            )
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.provisioning_error = None
        await self.session.flush()
        return subscription

    async def expire(self, subscription_id: str) -> Subscription:
        subscription = await self._get(subscription_id)
        subscription.status = SubscriptionStatus.EXPIRED
        await self.session.flush()
        logger.info(
            "subscription_expired",
            extra={"extra_fields": {"subscription_id": subscription.id}},
        )
        return subscription

    async def find_expired(self, *, limit: int = 100) -> list[Subscription]:
        """Active subscriptions that are past expiry or out of traffic.

        The scheduler calls this, then disables each one on its panel before
        marking it expired.
        """
        now = datetime.now(UTC)
        result = await self.session.scalars(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expire_at.is_not(None),
                Subscription.expire_at <= now,
            )
            .limit(limit)
        )
        return list(result)

    # -------------------------------------------------------------- reads
    async def list_for_user(
        self, user_id: str, *, status: SubscriptionStatus | None = None
    ) -> list[Subscription]:
        stmt = select(Subscription).where(Subscription.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Subscription.status == status)
        result = await self.session.scalars(stmt.order_by(Subscription.created_at.desc()))
        return list(result)

    async def list_active_provisioned(self, *, limit: int = 500) -> list[Subscription]:
        """Active subscriptions that have a panel account to query."""
        result = await self.session.scalars(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.panel_id.is_not(None),
                Subscription.panel_username.is_not(None),
            )
            .limit(limit)
        )
        return list(result)

    async def get_for_user(self, subscription_id: str, user_id: str) -> Subscription:
        subscription = await self.session.get(Subscription, subscription_id)
        if subscription is None or subscription.user_id != user_id:
            raise NotFoundError("Subscription not found", code="SUBSCRIPTION_NOT_FOUND")
        return subscription

    async def record_usage(self, subscription_id: str, used_bytes: int) -> Subscription:
        """Store usage reported by a panel, and suspend when the quota is gone."""
        subscription = await self._get(subscription_id)
        subscription.traffic_used_bytes = max(0, used_bytes)
        subscription.last_synced_at = datetime.now(UTC)

        if (
            subscription.status is SubscriptionStatus.ACTIVE
            and subscription.is_traffic_exhausted
        ):
            subscription.status = SubscriptionStatus.SUSPENDED
            subscription.provisioning_error = "Traffic quota exhausted"
            logger.info(
                "subscription_traffic_exhausted",
                extra={"extra_fields": {"subscription_id": subscription.id}},
            )

        await self.session.flush()
        return subscription

    # ---------------------------------------------------------- internals
    async def _get(self, subscription_id: str) -> Subscription:
        subscription = await self.session.get(Subscription, subscription_id)
        if subscription is None:
            raise NotFoundError("Subscription not found", code="SUBSCRIPTION_NOT_FOUND")
        return subscription

    @staticmethod
    def _is_past_expiry(subscription: Subscription) -> bool:
        if subscription.expire_at is None:
            return False
        expire_at = subscription.expire_at
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=UTC)
        return expire_at <= datetime.now(UTC)
