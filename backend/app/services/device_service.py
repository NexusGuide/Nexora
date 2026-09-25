"""Device registration and plan device limits (spec rule 12).

A plan sells a number of devices. Without enforcement that number is
decoration: a customer on a one-device plan can register twenty, and the
difference between plan tiers stops meaning anything.

The limit is a property of what the customer bought, so it comes from their
active subscriptions. Someone with no subscription can still sign in — they
have to, in order to buy one — but not unboundedly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.billing import Subscription
from app.models.enums import DeviceStatus, SubscriptionStatus
from app.models.user import RefreshToken, UserDevice

logger = logging.getLogger(__name__)

# What a user with no active subscription may register. Generous enough for a
# phone plus a tablet while browsing the store, low enough that an abandoned
# account cannot accumulate rows without limit.
UNSUBSCRIBED_DEVICE_ALLOWANCE = 3


class DeviceLimitReachedError(AppError):
    """Raised when a new device would exceed the plan's allowance.

    Carries the registered devices, so the app can show them and offer to
    revoke one rather than leaving the user stuck at a dead end.
    """

    status_code = 403
    code = "DEVICE_LIMIT_REACHED"
    message = "You have reached the device limit for your plan"


class DeviceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def effective_limit(self, user_id: str) -> int:
        """The highest device allowance across a user's active subscriptions.

        The highest rather than the sum: a customer holding a 1-device and a
        5-device plan should get 5. Summing would let someone stack cheap plans
        to beat the device cap of an expensive one.
        """
        best = await self.session.scalar(
            select(func.max(Subscription.device_limit)).where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
            )
        )
        if best is None:
            return UNSUBSCRIBED_DEVICE_ALLOWANCE
        # 0 is how a plan expresses "unlimited", consistent with traffic.
        return int(best) if best > 0 else 0

    async def active_devices(self, user_id: str) -> list[UserDevice]:
        result = await self.session.scalars(
            select(UserDevice)
            .where(
                UserDevice.user_id == user_id,
                UserDevice.status == DeviceStatus.ACTIVE,
            )
            .order_by(UserDevice.last_seen_at.desc().nullslast())
        )
        return list(result)

    async def register(
        self,
        user_id: str,
        *,
        device_id: str,
        device_name: str | None = None,
        app_version: str | None = None,
        platform: str = "android",
    ) -> UserDevice:
        """Record a device, enforcing the limit for a new one.

        A device already registered is always let through — re-authenticating
        on a device the user already owns must never be blocked, or a customer
        at their limit could be locked out of every device at once.
        """
        now = datetime.now(UTC)

        existing = await self.session.scalar(
            select(UserDevice).where(
                UserDevice.user_id == user_id, UserDevice.device_id == device_id
            )
        )
        if existing is not None:
            was_revoked = existing.status is DeviceStatus.REVOKED
            if was_revoked:
                # Re-activating counts as adding a device, so it must pass the
                # same check — otherwise revoke-then-return defeats the limit.
                await self._assert_capacity(user_id)
            existing.status = DeviceStatus.ACTIVE
            existing.last_seen_at = now
            if device_name:
                existing.device_name = device_name
            if app_version:
                existing.app_version = app_version
            await self.session.flush()
            return existing

        await self._assert_capacity(user_id)

        device = UserDevice(
            user_id=user_id,
            device_id=device_id,
            device_name=device_name or "Unknown device",
            platform=platform,
            app_version=app_version,
            last_seen_at=now,
            status=DeviceStatus.ACTIVE,
        )
        self.session.add(device)
        await self.session.flush()
        logger.info(
            "device_registered",
            extra={"extra_fields": {"user_id": user_id, "device_id": device.id}},
        )
        return device

    async def revoke(self, user_id: str, device_row_id: str) -> UserDevice | None:
        """Revoke one of the user's devices and end every session bound to it.

        Both in one step: revoking the row but leaving its refresh token
        working would let the "removed" device carry on as if nothing happened.
        """
        device = await self.session.get(UserDevice, device_row_id)
        if device is None or device.user_id != user_id:
            return None
        now = datetime.now(UTC)
        device.status = DeviceStatus.REVOKED
        device.last_seen_at = now

        tokens = await self.session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.device_id == device.device_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
        for token in tokens:
            token.revoked_at = now
            token.revoked_reason = "device_revoked"

        await self.session.flush()
        return device

    # ---------------------------------------------------------- internals
    async def _assert_capacity(self, user_id: str) -> None:
        limit = await self.effective_limit(user_id)
        if limit == 0:  # unlimited
            return

        devices = await self.active_devices(user_id)
        if len(devices) < limit:
            return

        logger.info(
            "device_limit_reached",
            extra={
                "extra_fields": {
                    "user_id": user_id,
                    "limit": limit,
                    "active": len(devices),
                }
            },
        )
        raise DeviceLimitReachedError(
            f"Your plan allows {limit} device(s). Remove one to add another.",
            details={
                "limit": limit,
                "active": len(devices),
                "devices": [
                    {
                        "id": d.id,
                        "name": d.device_name,
                        "platform": d.platform,
                        "last_seen_at": (
                            d.last_seen_at.isoformat() if d.last_seen_at else None
                        ),
                    }
                    for d in devices
                ],
            },
        )
