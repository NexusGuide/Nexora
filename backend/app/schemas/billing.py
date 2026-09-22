"""Plan, order and subscription contracts.

Traffic crosses the API in bytes, so clients never have to agree with the
server about what "GB" means. Convenience fields in GB are provided for
display, computed server-side so every client rounds the same way.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models.enums import (
    OrderStatus,
    PanelStatus,
    PanelType,
    PlanStatus,
    ServerStatus,
    SubscriptionStatus,
)

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


class ConfigPublic(BaseModel):
    """A config as returned to its owner.

    ``config_data`` is the actual connection URI. It is included because the
    VPN client needs it, and it is returned only on the owner's own request —
    never in a list belonging to somebody else, and never in a log.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    subscription_id: str
    name: str
    protocol: str | None = None
    host: str | None = None
    port: int | None = None
    config_data: str
    latency_ms: int | None = None
    is_active: bool
    updated_at: datetime


class PanelCreate(BaseModel):
    """Admin input for registering a panel.

    The credentials arrive here over HTTPS and are encrypted before they are
    stored. No response model echoes them back.
    """

    name: str = Field(..., min_length=1, max_length=128)
    panel_type: PanelType
    base_url: str = Field(..., min_length=8, max_length=512)
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    api_key: str | None = Field(default=None, max_length=512)
    verify_tls: bool = True

    @field_validator("base_url")
    @classmethod
    def _require_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v.rstrip("/")


class PanelPublic(BaseModel):
    """A panel as returned by the API.

    There is deliberately no field that could carry a credential: the schema
    exposes ``has_credentials`` instead, so a future endpoint cannot leak one
    by forgetting to exclude it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    panel_type: PanelType
    base_url: str
    status: PanelStatus
    verify_tls: bool
    last_checked_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime

    # Read from Panel.has_credentials on the ORM object: whether a credential
    # is set, never the credential itself.
    has_credentials: bool


class ServerCreate(BaseModel):
    panel_id: str = Field(..., max_length=36)
    name: str = Field(..., min_length=1, max_length=128)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    region: str | None = Field(default=None, max_length=64)
    group_id: str | None = Field(default=None, max_length=36)


class ServerPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    panel_id: str
    name: str
    host: str
    port: int
    region: str | None = None
    status: ServerStatus
    load_percent: int
    is_healthy: bool
    last_health_check_at: datetime | None = None
