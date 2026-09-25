"""Subscription state machine (spec rules 17-18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.exceptions import ConflictError
from app.models.billing import Plan
from app.models.enums import OrderStatus, PlanStatus, SubscriptionStatus
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService
from app.services.order_service import OrderService
from app.services.subscription_service import (
    SubscriptionService,
    generate_panel_username,
)

GB = 1024**3


async def setup_subscription(session, *, duration_days=30, traffic=100 * GB):
    user = await AuthService(session).register(
        RegisterRequest(username="buyer", password="CorrectHorse1")
    )
    plan = Plan(
        name="Test plan",
        duration_days=duration_days,
        traffic_limit_bytes=traffic,
        device_limit=3,
        price=Decimal("249000"),
        currency="IRT",
        status=PlanStatus.ACTIVE,
    )
    session.add(plan)
    await session.commit()

    orders = OrderService(session)
    order, _ = await orders.create_order(user_id=user.id, plan_id=plan.id)
    await session.commit()
    await orders.mark_paid(order.id)
    await session.commit()

    subs = SubscriptionService(session)
    subscription, _ = await subs.create_from_order(order.id)
    await session.commit()
    return user, plan, order, subscription


# --- activation -------------------------------------------------------------
async def test_activation_sets_the_window(session):
    _, _, _, sub = await setup_subscription(session, duration_days=30)
    service = SubscriptionService(session)

    activated = await service.activate(
        sub.id, panel_id="panel-1", panel_username="nx_abc", duration_days=30
    )
    await session.commit()

    assert activated.status is SubscriptionStatus.ACTIVE
    assert activated.start_at is not None
    assert activated.expire_at is not None
    assert 29 <= (activated.expire_at - activated.start_at).days <= 30


async def test_activation_is_idempotent(session):
    _, _, _, sub = await setup_subscription(session)
    service = SubscriptionService(session)

    first = await service.activate(
        sub.id, panel_id="p1", panel_username="nx_a", duration_days=30
    )
    await session.commit()
    expiry = first.expire_at

    second = await service.activate(
        sub.id, panel_id="p1", panel_username="nx_a", duration_days=30
    )
    await session.commit()

    # A retried activation must not silently extend the subscription.
    assert second.expire_at == expiry


async def test_panel_username_reveals_nothing_about_the_user(session):
    """The panel is shared infrastructure; customer identity stays out of it."""
    name = generate_panel_username("11111111-2222-3333-4444-555555555555")
    assert name.startswith("nx_")
    assert "buyer" not in name
    assert generate_panel_username("same-id") != generate_panel_username("same-id")


# --- renewal ----------------------------------------------------------------
async def test_early_renewal_extends_from_current_expiry(session):
    """Renewing early must not cost the customer the days they already paid."""
    user, plan, _, sub = await setup_subscription(session, duration_days=30)
    service = SubscriptionService(session)

    await service.activate(sub.id, panel_id="p1", panel_username="nx_a", duration_days=30)
    await session.commit()
    original_expiry = sub.expire_at

    orders = OrderService(session)
    renewal, _ = await orders.create_order(
        user_id=user.id,
        plan_id=plan.id,
        client_idempotency_key="renew-001",
        subscription_id=sub.id,
    )
    await session.commit()
    await orders.mark_paid(renewal.id)
    await session.commit()

    await service.renew(sub.id, order_id=renewal.id)
    await session.commit()

    assert sub.expire_at > original_expiry
    assert (sub.expire_at - original_expiry).days >= 29


async def test_renewing_an_expired_subscription_restarts_from_now(session):
    user, plan, _, sub = await setup_subscription(session, duration_days=30)
    service = SubscriptionService(session)

    await service.activate(sub.id, panel_id="p1", panel_username="nx_a", duration_days=30)
    sub.expire_at = datetime.now(UTC) - timedelta(days=10)
    sub.status = SubscriptionStatus.EXPIRED
    await session.commit()

    orders = OrderService(session)
    renewal, _ = await orders.create_order(
        user_id=user.id,
        plan_id=plan.id,
        client_idempotency_key="renew-002",
        subscription_id=sub.id,
    )
    await session.commit()
    await orders.mark_paid(renewal.id)
    await session.commit()

    await service.renew(sub.id, order_id=renewal.id)
    await session.commit()

    assert sub.status is SubscriptionStatus.ACTIVE
    assert sub.expire_at > datetime.now(UTC) + timedelta(days=29)
    assert sub.traffic_used_bytes == 0


async def test_renewal_requires_a_paid_order(session):
    user, plan, _, sub = await setup_subscription(session)
    orders = OrderService(session)
    unpaid, _ = await orders.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key="unpaid-1"
    )
    await session.commit()

    with pytest.raises(ConflictError) as exc:
        await SubscriptionService(session).renew(sub.id, order_id=unpaid.id)
    assert exc.value.code == "ORDER_NOT_PAID"


async def test_a_renewal_order_extends_rather_than_duplicating(session):
    """A renewal must never hand the customer a second subscription."""
    user, plan, _, sub = await setup_subscription(session)
    service = SubscriptionService(session)
    orders = OrderService(session)

    await service.activate(sub.id, panel_id="p1", panel_username="nx_a", duration_days=30)
    await session.commit()

    renewal, _ = await orders.create_order(
        user_id=user.id,
        plan_id=plan.id,
        client_idempotency_key="renew-003",
        subscription_id=sub.id,
    )
    await session.commit()
    await orders.mark_paid(renewal.id)
    await session.commit()

    returned, created = await service.create_from_order(renewal.id)
    await session.commit()

    assert created is False
    assert returned.id == sub.id


# --- suspend / resume / expire ---------------------------------------------
async def test_suspend_and_resume(session):
    _, _, _, sub = await setup_subscription(session)
    service = SubscriptionService(session)
    await service.activate(sub.id, panel_id="p1", panel_username="nx_a")
    await session.commit()

    await service.suspend(sub.id, "payment reversed")
    await session.commit()
    assert sub.status is SubscriptionStatus.SUSPENDED

    await service.resume(sub.id)
    await session.commit()
    assert sub.status is SubscriptionStatus.ACTIVE


async def test_expired_subscription_cannot_be_resumed(session):
    _, _, _, sub = await setup_subscription(session)
    service = SubscriptionService(session)
    await service.activate(sub.id, panel_id="p1", panel_username="nx_a")
    await service.suspend(sub.id, "quota")
    sub.expire_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()

    with pytest.raises(ConflictError) as exc:
        await service.resume(sub.id)
    assert exc.value.code == "SUBSCRIPTION_EXPIRED"


async def test_pending_subscription_cannot_be_suspended(session):
    _, _, _, sub = await setup_subscription(session)
    with pytest.raises(ConflictError):
        await SubscriptionService(session).suspend(sub.id, "nope")


# --- traffic ----------------------------------------------------------------
async def test_exhausted_traffic_suspends_the_subscription(session):
    _, _, _, sub = await setup_subscription(session, traffic=10 * GB)
    service = SubscriptionService(session)
    await service.activate(sub.id, panel_id="p1", panel_username="nx_a")
    await session.commit()

    await service.record_usage(sub.id, 5 * GB)
    await session.commit()
    assert sub.status is SubscriptionStatus.ACTIVE
    assert sub.traffic_remaining_bytes == 5 * GB

    await service.record_usage(sub.id, 10 * GB)
    await session.commit()
    assert sub.status is SubscriptionStatus.SUSPENDED
    assert sub.traffic_remaining_bytes == 0


async def test_unlimited_traffic_is_never_exhausted(session):
    _, _, _, sub = await setup_subscription(session, traffic=0)
    service = SubscriptionService(session)
    await service.activate(sub.id, panel_id="p1", panel_username="nx_a")
    await session.commit()

    await service.record_usage(sub.id, 900 * GB)
    await session.commit()

    assert sub.status is SubscriptionStatus.ACTIVE
    assert sub.is_unlimited_traffic
    assert sub.traffic_remaining_bytes is None


async def test_find_expired_returns_only_active_past_expiry(session):
    _, _, _, sub = await setup_subscription(session)
    service = SubscriptionService(session)
    await service.activate(sub.id, panel_id="p1", panel_username="nx_a")
    await session.commit()

    assert await service.find_expired() == []

    sub.expire_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()

    due = await service.find_expired()
    assert [s.id for s in due] == [sub.id]

    await service.expire(sub.id)
    await session.commit()
    assert await service.find_expired() == []


async def test_order_status_after_full_flow(session):
    _, _, order, _ = await setup_subscription(session)
    assert order.status is OrderStatus.PAID
    assert order.completed_at is not None


def test_days_remaining_rounds_up_a_part_day():
    from datetime import UTC, datetime, timedelta

    from app.schemas.billing import SubscriptionPublic

    def days_left(delta):
        now = datetime.now(UTC)
        return SubscriptionPublic.model_construct(expire_at=now + delta).days_remaining

    # Bought a minute ago: still the full 30 days, not 29.
    assert days_left(timedelta(days=30) - timedelta(minutes=1)) == 30
    assert days_left(timedelta(hours=3)) == 1
    assert days_left(timedelta(seconds=-5)) == 0
