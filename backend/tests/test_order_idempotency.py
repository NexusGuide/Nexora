"""Orders must never charge or provision twice (spec rules 19 and 56)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.billing import Order, Plan, Subscription
from app.models.enums import OrderStatus, PlanStatus, SubscriptionStatus
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService
from app.services.order_service import OrderService, plan_from_snapshot
from app.services.subscription_service import SubscriptionService

GB = 1024**3


async def make_user(session, username="buyer"):
    user = await AuthService(session).register(
        RegisterRequest(username=username, password="CorrectHorse1")
    )
    await session.commit()
    return user


async def make_plan(session, **kw) -> Plan:
    plan = Plan(
        name=kw.get("name", "100GB / 30 days"),
        duration_days=kw.get("duration_days", 30),
        traffic_limit_bytes=kw.get("traffic_limit_bytes", 100 * GB),
        device_limit=kw.get("device_limit", 3),
        price=Decimal(kw.get("price", "249000")),
        currency="IRT",
        status=kw.get("status", PlanStatus.ACTIVE),
    )
    session.add(plan)
    await session.commit()
    return plan


# --- creation ---------------------------------------------------------------
async def test_order_captures_the_plan_price(session):
    user = await make_user(session)
    plan = await make_plan(session)

    order, created = await OrderService(session).create_order(
        user_id=user.id, plan_id=plan.id
    )
    await session.commit()

    assert created is True
    assert order.status is OrderStatus.PENDING
    assert Decimal(str(order.amount)) == Decimal("249000")
    assert order.currency == "IRT"


async def test_same_idempotency_key_returns_the_same_order(session):
    """The core guarantee: a retried submission must not create a second order."""
    user = await make_user(session)
    plan = await make_plan(session)
    service = OrderService(session)

    first, created_first = await service.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key="checkout-abc-123"
    )
    await session.commit()

    second, created_second = await service.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key="checkout-abc-123"
    )
    await session.commit()

    assert created_first is True
    assert created_second is False
    assert first.id == second.id

    count = await session.scalar(select(func.count()).select_from(Order))
    assert count == 1


async def test_different_keys_create_different_orders(session):
    """A genuine second purchase must still be possible once the first is paid."""
    user = await make_user(session)
    plan = await make_plan(session)
    service = OrderService(session)

    first, _ = await service.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key="checkout-aaa-111"
    )
    await service.mark_paid(first.id)
    await session.commit()
    second, created = await service.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key="checkout-bbb-222"
    )
    await session.commit()

    assert created is True
    assert first.id != second.id


async def test_keys_are_scoped_per_user(session):
    """Two users may legitimately send the same client-side key."""
    alice = await make_user(session, "alice")
    bob = await make_user(session, "bob")
    plan = await make_plan(session)
    service = OrderService(session)

    a, _ = await service.create_order(
        user_id=alice.id, plan_id=plan.id, client_idempotency_key="same-key-000"
    )
    await session.commit()
    b, created = await service.create_order(
        user_id=bob.id, plan_id=plan.id, client_idempotency_key="same-key-000"
    )
    await session.commit()

    assert created is True
    assert a.id != b.id


async def test_hidden_plan_cannot_be_ordered(session):
    user = await make_user(session)
    plan = await make_plan(session, status=PlanStatus.HIDDEN)

    with pytest.raises(ValidationError) as exc:
        await OrderService(session).create_order(user_id=user.id, plan_id=plan.id)
    assert exc.value.code == "PLAN_UNAVAILABLE"


async def test_unknown_plan_is_rejected(session):
    user = await make_user(session)
    with pytest.raises(NotFoundError):
        await OrderService(session).create_order(user_id=user.id, plan_id="nope")


# --- the snapshot -----------------------------------------------------------
async def test_editing_a_plan_does_not_change_a_placed_order(session):
    """A price change must not rewrite what an existing customer bought."""
    user = await make_user(session)
    plan = await make_plan(session, price="249000", traffic_limit_bytes=100 * GB)

    order, _ = await OrderService(session).create_order(user_id=user.id, plan_id=plan.id)
    await session.commit()

    plan.price = Decimal("499000")
    plan.traffic_limit_bytes = 50 * GB
    await session.commit()

    terms = plan_from_snapshot(order)
    assert terms["price"] == "249000"
    assert terms["traffic_limit_bytes"] == 100 * GB


# --- paying -----------------------------------------------------------------
async def test_order_is_paid_exactly_once(session):
    """A replayed gateway callback must not re-trigger provisioning."""
    user = await make_user(session)
    plan = await make_plan(session)
    service = OrderService(session)

    order, _ = await service.create_order(user_id=user.id, plan_id=plan.id)
    await session.commit()

    _, first = await service.mark_paid(order.id)
    await session.commit()
    _, second = await service.mark_paid(order.id)
    await session.commit()
    _, third = await service.mark_paid(order.id)
    await session.commit()

    assert first is True
    assert second is False and third is False
    assert order.status is OrderStatus.PAID


async def test_cancelled_order_cannot_be_paid(session):
    user = await make_user(session)
    plan = await make_plan(session)
    service = OrderService(session)

    order, _ = await service.create_order(user_id=user.id, plan_id=plan.id)
    await session.commit()
    await service.cancel_order(order.id, user.id)
    await session.commit()

    with pytest.raises(ConflictError) as exc:
        await service.mark_paid(order.id)
    assert exc.value.code == "ORDER_NOT_PAYABLE"


async def test_paid_order_cannot_be_cancelled_or_failed(session):
    """Money has moved; neither path may quietly undo that."""
    user = await make_user(session)
    plan = await make_plan(session)
    service = OrderService(session)

    order, _ = await service.create_order(user_id=user.id, plan_id=plan.id)
    await session.commit()
    await service.mark_paid(order.id)
    await session.commit()

    with pytest.raises(ConflictError):
        await service.cancel_order(order.id, user.id)
    with pytest.raises(ConflictError):
        await service.mark_failed(order.id, "gateway timeout")


async def test_another_users_order_is_not_visible(session):
    """Reported as missing, not forbidden — 403 would confirm it exists."""
    alice = await make_user(session, "alice")
    bob = await make_user(session, "bob")
    plan = await make_plan(session)

    order, _ = await OrderService(session).create_order(user_id=alice.id, plan_id=plan.id)
    await session.commit()

    with pytest.raises(NotFoundError):
        await OrderService(session).get_order(order.id, bob.id)


# --- provisioning -----------------------------------------------------------
async def test_paid_order_provisions_one_subscription_however_often_retried(session):
    """The retry-safety that the whole design exists to provide."""
    user = await make_user(session)
    plan = await make_plan(session)
    orders = OrderService(session)
    subs = SubscriptionService(session)

    order, _ = await orders.create_order(user_id=user.id, plan_id=plan.id)
    await session.commit()
    await orders.mark_paid(order.id)
    await session.commit()

    first, created_first = await subs.create_from_order(order.id)
    await session.commit()
    _, created_second = await subs.create_from_order(order.id)
    await session.commit()
    _, created_third = await subs.create_from_order(order.id)
    await session.commit()

    assert created_first is True
    assert created_second is False and created_third is False

    count = await session.scalar(select(func.count()).select_from(Subscription))
    assert count == 1
    assert first.traffic_limit_bytes == 100 * GB
    assert first.device_limit == 3
    assert first.status is SubscriptionStatus.PENDING


async def test_unpaid_order_cannot_be_provisioned(session):
    user = await make_user(session)
    plan = await make_plan(session)

    order, _ = await OrderService(session).create_order(user_id=user.id, plan_id=plan.id)
    await session.commit()

    with pytest.raises(ConflictError) as exc:
        await SubscriptionService(session).create_from_order(order.id)
    assert exc.value.code == "ORDER_NOT_PAID"


async def test_buying_again_while_unpaid_returns_the_waiting_order(session):
    # Found on the first real purchase: "Buy" tapped twice left two PENDING
    # orders for one payment, and nothing said which the money was for.
    user = await make_user(session)
    plan = await make_plan(session)
    service = OrderService(session)

    first, _ = await service.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key="checkout-aaa-111"
    )
    await session.commit()
    again, created = await service.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key="checkout-bbb-222"
    )

    assert created is False
    assert again.id == first.id


async def test_a_cancelled_order_does_not_block_a_new_one(session):
    user = await make_user(session)
    plan = await make_plan(session)
    service = OrderService(session)

    first, _ = await service.create_order(user_id=user.id, plan_id=plan.id)
    await service.cancel_order(first.id, user.id)
    await session.commit()
    second, created = await service.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key="checkout-ccc-333"
    )

    assert created is True
    assert second.id != first.id
