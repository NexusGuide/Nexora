"""Domain enumerations shared across models, schemas and adapters."""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    BANNED = "BANNED"


class DeviceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class PlanStatus(StrEnum):
    ACTIVE = "ACTIVE"
    HIDDEN = "HIDDEN"
    ARCHIVED = "ARCHIVED"


class SubscriptionStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class PanelType(StrEnum):
    PASARGUARD = "PASARGUARD"
    XUI = "XUI"
    MARZBAN = "MARZBAN"
    CUSTOM = "CUSTOM"


class PanelStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    UNREACHABLE = "UNREACHABLE"


class ServerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    MAINTENANCE = "MAINTENANCE"
    DISABLED = "DISABLED"


class AdminRole(StrEnum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    FINANCE = "FINANCE"
    SUPPORT = "SUPPORT"
    DEVELOPER = "DEVELOPER"
