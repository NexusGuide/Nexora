"""Order lifecycle.

This is the money path, so two rules from the spec govern everything here:

- Rule 19/56: orders and payment processing must be idempotent. A successful
  payment must never create the same service twice.
- Rule 56: use database transactions and locks; do not rely on a simple
  boolean to control financial operations.

Both are enforced with database constraints rather than Python checks, because
a ``SELECT`` followed by an ``INSERT`` is not atomic and two concurrent
requests can both pass the check.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.billing import Order, Payment, Plan, Subscription
from app.models.enums import OrderStatus, PaymentStatus, PlanStatus

logger = logging.getLogger(__name__)


def build_idempotency_key(
    user_id: str, plan_id: str, client_key: str | None, subscription_id: str | None
) -> str:
    """Derive the key that de-duplicates an order.

    When the client supplies a key, that alone identifies the request — the
    client owns retry semantics. When it does not, we fall back to a hash of
    (user, plan, subscription, minute), which collapses accidental
    double-submits from a tapped button without blocking a genuine second
    purchase a minute later.
    """
    if client_key:
        return f"c:{hashlib.sha256(f'{user_id}:{client_key}'.encode()).hexdigest()[:48]}"

    minute_bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")
    raw = f"{user_id}:{plan_id}:{subscription_id or '-'}:{minute_bucket}"
    return f"a:{hashlib.sha256(raw.encode()).hexdigest()[:48]}"


class OrderService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ----------------------------------------------------------------- create
    async def create_order(
        self,
        *,
        user_id: str,
        plan_id: str,
        client_idempotency_key: str | None = None,
        subscription_id: str | None = None,
    ) -> tuple[Order, bool]:
        """Create an order, or return the existing one for a repeated request.

        Returns ``(order, created)``. ``created`` is ``False`` when this call
        matched an earlier order, which lets the endpoint answer 200 rather
        than 201 without the caller having to care.
        """
        plan = await self.session.get(Plan, plan_id)
        if plan is None:
            raise NotFoundError("Plan not found", code="PLAN_NOT_FOUND")
        if plan.status is not PlanStatus.ACTIVE:
            raise ValidationError(
                "This plan is not available for purchase",
                code="PLAN_UNAVAILABLE",
            )

        if subscription_id is not None:
            subscription = await self.session.get(Subscription, subscription_id)
            if subscription is None or subscription.user_id != user_id:
                raise NotFoundError(
                    "Subscription not found", code="SUBSCRIPTION_NOT_FOUND"
                )

        key = build_idempotency_key(
            user_id, plan_id, client_idempotency_key, subscription_id
        )

        existing = await self.session.scalar(
            select(Order).where(Order.idempotency_key == key)
        )
        if existing is not None:
            return existing, False

        order = Order(
            user_id=user_id,
            plan_id=plan_id,
            subscription_id=subscription_id,
            amount=Decimal(str(plan.price)),
            currency=plan.currency,
            status=OrderStatus.PENDING,
            idempotency_key=key,
            plan_snapshot=json.dumps(
                {
                    "plan_id": plan.id,
                    "name": plan.name,
                    "duration_days": plan.duration_days,
                    "traffic_limit_bytes": plan.traffic_limit_bytes,
                    "device_limit": plan.device_limit,
                    "price": str(plan.price),
                    "currency": plan.currency,
                    "captured_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
            ),
        )
        self.session.add(order)

        try:
            await self.session.flush()
        except IntegrityError:
            # Lost the race against a concurrent identical request. The other
            # transaction's order is the canonical one; serve that.
            await self.session.rollback()
            duplicate = await self.session.scalar(
                select(Order).where(Order.idempotency_key == key)
            )
            if duplicate is None:
                raise
            logger.info(
                "order_create_raced",
                extra={"extra_fields": {"order_id": duplicate.id}},
            )
            return duplicate, False

        logger.info(
            "order_created",
            extra={"extra_fields": {"order_id": order.id, "plan_id": plan_id}},
        )
        return order, True

    # ------------------------------------------------------------------ read
    async def get_order(self, order_id: str, user_id: str) -> Order:
        order = await self.session.get(Order, order_id)
        # A foreign order is reported as missing, not forbidden: "403" would
        # confirm the id exists and belongs to somebody.
        if order is None or order.user_id != user_id:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
        return order

    async def list_orders(
        self, user_id: str, *, limit: int = 20, offset: int = 0
    ) -> list[Order]:
        result = await self.session.scalars(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result)

    # ---------------------------------------------------------------- cancel
    async def cancel_order(self, order_id: str, user_id: str) -> Order:
        order = await self._lock_order(order_id, user_id)
        if order.status is not OrderStatus.PENDING:
            raise ConflictError(
                f"An order that is {order.status.value.lower()} cannot be cancelled",
                code="ORDER_NOT_CANCELLABLE",
            )
        order.status = OrderStatus.CANCELLED
        order.completed_at = datetime.now(UTC)
        await self.session.flush()
        return order

    # ------------------------------------------------------------ mark paid
    async def mark_paid(
        self,
        order_id: str,
        *,
        payment_id: str | None = None,
        transaction_id: str | None = None,
    ) -> tuple[Order, bool]:
        """Move an order to PAID exactly once.

        Returns ``(order, transitioned)``. ``transitioned`` is ``False`` when
        the order was already paid, which is the normal outcome of a gateway
        retrying its callback — and the point at which a naive implementation
        would provision a second subscription.

        The row is locked for update first, so two concurrent callbacks
        serialise instead of both observing PENDING.
        """
        order = await self._lock_order(order_id)

        if order.status is OrderStatus.PAID:
            logger.info(
                "order_already_paid",
                extra={"extra_fields": {"order_id": order.id}},
            )
            return order, False

        if order.status is not OrderStatus.PENDING:
            raise ConflictError(
                f"An order that is {order.status.value.lower()} cannot be paid",
                code="ORDER_NOT_PAYABLE",
            )

        order.status = OrderStatus.PAID
        order.completed_at = datetime.now(UTC)

        if payment_id is not None:
            payment = await self.session.get(Payment, payment_id)
            if payment is not None:
                payment.status = PaymentStatus.VERIFIED
                payment.paid_at = order.completed_at
                if transaction_id:
                    payment.transaction_id = transaction_id

        await self.session.flush()
        logger.info("order_paid", extra={"extra_fields": {"order_id": order.id}})
        return order, True

    async def mark_failed(self, order_id: str, reason: str) -> Order:
        order = await self._lock_order(order_id)
        if order.status is OrderStatus.PAID:
            # Never downgrade a paid order: money has already moved.
            raise ConflictError(
                "A paid order cannot be marked failed",
                code="ORDER_ALREADY_PAID",
            )
        order.status = OrderStatus.FAILED
        order.failure_reason = reason[:255]
        order.completed_at = datetime.now(UTC)
        await self.session.flush()
        return order

    # -------------------------------------------------------------- internals
    async def _lock_order(self, order_id: str, user_id: str | None = None) -> Order:
        """Fetch an order with a row lock held to the end of the transaction.

        SQLite ignores ``FOR UPDATE`` but serialises writers anyway; on
        PostgreSQL this is what makes the paid-once transition safe.
        """
        stmt = select(Order).where(Order.id == order_id).with_for_update()
        order = await self.session.scalar(stmt)
        if order is None or (user_id is not None and order.user_id != user_id):
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
        return order


def plan_from_snapshot(order: Order) -> dict:
    """The plan terms as they stood when the order was placed.

    Provisioning reads this rather than the live plan row, so an admin editing
    a plan does not change what an already-placed order delivers.
    """
    return json.loads(order.plan_snapshot)


__all__ = ["OrderService", "build_idempotency_key", "plan_from_snapshot"]
