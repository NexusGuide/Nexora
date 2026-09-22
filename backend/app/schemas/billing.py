"""Plan, order and subscription contracts.

Traffic crosses the API in bytes, so clients never have to agree with the
server about what "GB" means. Convenience fields in GB are provided for
display, computed server-side so every client rounds the same way.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import OrderStatus, PlanStatus, SubscriptionStatus

BYTES_PER_GB = 1024**3


class PlanPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    duration_days: int
    traffic_limit_bytes: int
    device_limit: int
    price: Decimal
    currency: str
    status: PlanStatus

    @computed_field
    @property
    def traffic_limit_gb(self) -> float | None:
        """``None`` means unlimited."""
        if self.traffic_limit_bytes == 0:
            return None
        return round(self.traffic_limit_bytes / BYTES_PER_GB, 2)


class OrderCreate(BaseModel):
    plan_id: str = Field(..., min_length=1, max_length=36)

    # Supplied by the client to make a retry safe. Two requests with the same
    # key return the same order rather than charging twice.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)

    # Present when this order renews an existing subscription.
    subscription_id: str | None = Field(default=None, max_length=36)


class OrderPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_id: str
    subscription_id: str | None = None
    amount: Decimal
    currency: str
    status: OrderStatus
    created_at: datetime
    completed_at: datetime | None = None
    failure_reason: str | None = None


class SubscriptionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_id: str
    status: SubscriptionStatus
    start_at: datetime | None = None
    expire_at: datetime | None = None
    traffic_limit_bytes: int
    traffic_used_bytes: int
    device_limit: int
    created_at: datetime

    # panel_id, panel_username and subscription_url are deliberately absent:
    # they describe infrastructure the customer has no business seeing, and
    # the subscription URL is delivered through the configs endpoint instead.

    @computed_field
    @property
    def traffic_limit_gb(self) -> float | None:
        if self.traffic_limit_bytes == 0:
            return None
        return round(self.traffic_limit_bytes / BYTES_PER_GB, 2)

    @computed_field
    @property
    def traffic_used_gb(self) -> float:
        return round(self.traffic_used_bytes / BYTES_PER_GB, 2)

    @computed_field
    @property
    def traffic_remaining_bytes(self) -> int | None:
        if self.traffic_limit_bytes == 0:
            return None
        return max(0, self.traffic_limit_bytes - self.traffic_used_bytes)

    @computed_field
    @property
    def days_remaining(self) -> int | None:
        if self.expire_at is None:
            return None
        from datetime import UTC

        expire_at = self.expire_at
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=UTC)
        delta = expire_at - datetime.now(UTC)
        return max(0, delta.days)


class DevicePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_name: str
    platform: str
    app_version: str | None = None
    last_seen_at: datetime | None = None
    created_at: datetime


class UserUpdate(BaseModel):
    """Fields a user may change about themselves.

    Username and status are absent on purpose: the first is an identifier
    others may rely on, the second is an administrative decision.
    """

    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
