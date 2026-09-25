"""A plan's device count must actually limit devices."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.billing import Plan, Subscription
from app.models.enums import DeviceStatus, PlanStatus, SubscriptionStatus
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.auth_service import AuthService
from app.services.device_service import (
    UNSUBSCRIBED_DEVICE_ALLOWANCE,
    DeviceLimitReachedError,
    DeviceService,
)

GB = 1024**3
PASSWORD = "CorrectHorse1"


async def make_user(session, username="buyer"):
    user = await AuthService(session).register(
        RegisterRequest(username=username, password=PASSWORD)
    )
    await session.commit()
    return user


async def give_subscription(session, user, device_limit: int):
    plan = Plan(
        name=f"{device_limit} devices",
        duration_days=30,
        traffic_limit_bytes=100 * GB,
        device_limit=device_limit,
        price=Decimal("100000"),
        currency="IRT",
        status=PlanStatus.ACTIVE,
    )
    session.add(plan)
    await session.flush()

    subscription = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        device_limit=device_limit,
        traffic_limit_bytes=100 * GB,
        status=SubscriptionStatus.ACTIVE,
        expire_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(subscription)
    await session.commit()
    return subscription


# --- the limit --------------------------------------------------------------
async def test_limit_comes_from_the_active_subscription(session):
    user = await make_user(session)
    service = DeviceService(session)

    assert await service.effective_limit(user.id) == UNSUBSCRIBED_DEVICE_ALLOWANCE

    await give_subscription(session, user, device_limit=3)
    assert await service.effective_limit(user.id) == 3


async def test_the_highest_plan_wins_rather_than_the_sum(session):
    """Summing would let someone stack cheap plans to beat an expensive cap."""
    user = await make_user(session)
    await give_subscription(session, user, device_limit=1)
    await give_subscription(session, user, device_limit=5)

    assert await DeviceService(session).effective_limit(user.id) == 5


async def test_zero_means_unlimited(session):
    user = await make_user(session)
    await give_subscription(session, user, device_limit=0)
    service = DeviceService(session)

    assert await service.effective_limit(user.id) == 0
    for n in range(8):
        await service.register(user.id, device_id=f"dev-{n}")
    await session.commit()
    assert len(await service.active_devices(user.id)) == 8


async def test_expired_subscription_does_not_grant_its_limit(session):
    user = await make_user(session)
    subscription = await give_subscription(session, user, device_limit=5)
    subscription.status = SubscriptionStatus.EXPIRED
    await session.commit()

    assert (
        await DeviceService(session).effective_limit(user.id)
        == UNSUBSCRIBED_DEVICE_ALLOWANCE
    )


# --- enforcement ------------------------------------------------------------
async def test_registering_beyond_the_limit_is_refused(session):
    user = await make_user(session)
    await give_subscription(session, user, device_limit=2)
    service = DeviceService(session)

    await service.register(user.id, device_id="phone", device_name="Pixel")
    await service.register(user.id, device_id="tablet", device_name="iPad")
    await session.commit()

    with pytest.raises(DeviceLimitReachedError) as exc:
        await service.register(user.id, device_id="laptop", device_name="ThinkPad")

    assert exc.value.status_code == 403
    assert exc.value.details["limit"] == 2
    # The app needs the list to offer a revoke rather than a dead end.
    assert len(exc.value.details["devices"]) == 2
    assert {d["name"] for d in exc.value.details["devices"]} == {"Pixel", "iPad"}


async def test_a_known_device_is_never_blocked(session):
    """Otherwise a user at their limit could be locked out of every device."""
    user = await make_user(session)
    await give_subscription(session, user, device_limit=1)
    service = DeviceService(session)

    await service.register(user.id, device_id="phone", device_name="Pixel")
    await session.commit()

    again = await service.register(
        user.id, device_id="phone", device_name="Pixel", app_version="1.2.0"
    )
    await session.commit()

    assert again.app_version == "1.2.0"
    assert len(await service.active_devices(user.id)) == 1


async def test_revoking_frees_a_slot(session):
    user = await make_user(session)
    await give_subscription(session, user, device_limit=1)
    service = DeviceService(session)

    first = await service.register(user.id, device_id="phone")
    await session.commit()

    with pytest.raises(DeviceLimitReachedError):
        await service.register(user.id, device_id="tablet")

    await service.revoke(user.id, first.id)
    await session.commit()

    replacement = await service.register(user.id, device_id="tablet")
    await session.commit()
    assert replacement.status is DeviceStatus.ACTIVE
    assert len(await service.active_devices(user.id)) == 1


async def test_revoke_then_return_still_faces_the_limit(session):
    """Re-activating is adding a device; it must not be a way around the cap."""
    user = await make_user(session)
    await give_subscription(session, user, device_limit=1)
    service = DeviceService(session)

    old = await service.register(user.id, device_id="old-phone")
    await service.revoke(user.id, old.id)
    await service.register(user.id, device_id="new-phone")
    await session.commit()

    with pytest.raises(DeviceLimitReachedError):
        await service.register(user.id, device_id="old-phone")


async def test_a_user_without_a_subscription_can_still_sign_in(session):
    """They have to be able to log in to buy something."""
    user = await make_user(session)
    service = DeviceService(session)

    for n in range(UNSUBSCRIBED_DEVICE_ALLOWANCE):
        await service.register(user.id, device_id=f"dev-{n}")
    await session.commit()

    with pytest.raises(DeviceLimitReachedError):
        await service.register(user.id, device_id="one-too-many")


# --- through the login path -------------------------------------------------
async def test_login_enforces_the_limit(session):
    user = await make_user(session)
    await give_subscription(session, user, device_limit=1)
    auth = AuthService(session)

    await auth.login(
        LoginRequest(identifier="buyer", password=PASSWORD, device_id="phone")
    )
    await session.commit()

    with pytest.raises(DeviceLimitReachedError):
        await auth.login(
            LoginRequest(identifier="buyer", password=PASSWORD, device_id="tablet")
        )


async def test_login_without_a_device_id_is_unaffected(session):
    """A web or admin sign-in registers nothing and must not be capped."""
    user = await make_user(session)
    await give_subscription(session, user, device_limit=1)
    auth = AuthService(session)

    await auth.login(
        LoginRequest(identifier="buyer", password=PASSWORD, device_id="phone")
    )
    await session.commit()

    result = await auth.login(LoginRequest(identifier="buyer", password=PASSWORD))
    await session.commit()
    assert result.tokens.access_token


# --- replacing a device at sign-in ---------------------------------------------
# Found on a real phone: a reinstall gets a new device id, and the old install
# held the only slot, so the owner of both could not sign in anywhere.
async def test_sign_in_can_replace_a_listed_device(session):
    from sqlalchemy import select

    from app.models.user import RefreshToken, UserDevice

    user = await make_user(session)
    await give_subscription(session, user, device_limit=1)
    auth = AuthService(session)
    await auth.login(LoginRequest(identifier="buyer", password=PASSWORD, device_id="old"))
    await session.commit()

    with pytest.raises(DeviceLimitReachedError) as refused:
        await auth.login(
            LoginRequest(identifier="buyer", password=PASSWORD, device_id="new")
        )
    await session.rollback()
    old_row = refused.value.details["devices"][0]["id"]

    result = await auth.login(
        LoginRequest(
            identifier="buyer", password=PASSWORD, device_id="new", replace_device=old_row
        )
    )
    await session.commit()
    assert result.tokens.access_token

    old = await session.get(UserDevice, old_row)
    assert old.status is DeviceStatus.REVOKED
    # The replaced install's sessions end with it.
    live = await session.scalars(
        select(RefreshToken).where(
            RefreshToken.device_id == "old", RefreshToken.revoked_at.is_(None)
        )
    )
    assert list(live) == []


async def test_a_wrong_password_cannot_replace_a_device(session):
    from app.core.exceptions import AuthenticationError
    from app.models.user import UserDevice

    user = await make_user(session)
    await give_subscription(session, user, device_limit=1)
    auth = AuthService(session)
    await auth.login(LoginRequest(identifier="buyer", password=PASSWORD, device_id="old"))
    await session.commit()
    old_row = (await DeviceService(session).active_devices(user.id))[0].id

    with pytest.raises(AuthenticationError):
        await auth.login(
            LoginRequest(
                identifier="buyer",
                password="Wrong-Passw0rd-for-tests-only",
                device_id="new",
                replace_device=old_row,
            )
        )
    await session.rollback()
    assert (await session.get(UserDevice, old_row)).status is DeviceStatus.ACTIVE


async def test_another_users_device_cannot_be_replaced(session):
    from app.core.exceptions import NotFoundError

    victim = await make_user(session, username="victim")
    await give_subscription(session, victim, device_limit=1)
    auth = AuthService(session)
    await auth.login(LoginRequest(identifier="victim", password=PASSWORD, device_id="v"))
    await session.commit()
    victim_row = (await DeviceService(session).active_devices(victim.id))[0].id

    attacker = await make_user(session, username="attacker")
    await give_subscription(session, attacker, device_limit=1)
    with pytest.raises(NotFoundError):
        await auth.login(
            LoginRequest(
                identifier="attacker",
                password=PASSWORD,
                device_id="a",
                replace_device=victim_row,
            )
        )
