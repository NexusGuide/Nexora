"""SQLAlchemy models.

Importing this package registers every table on ``Base.metadata`` — which is
what Alembic autogenerate relies on.
"""

from app.models.enums import (
    AdminRole,
    DeviceStatus,
    OrderStatus,
    PanelStatus,
    PanelType,
    PaymentStatus,
    PlanStatus,
    ServerStatus,
    SubscriptionStatus,
    UserStatus,
)
from app.models.panel import AuditLog, Panel, Server, ServerGroup
from app.models.user import LoginAttempt, RefreshToken, User, UserDevice

__all__ = [
    "AdminRole",
    "AuditLog",
    "DeviceStatus",
    "LoginAttempt",
    "OrderStatus",
    "Panel",
    "PanelStatus",
    "PanelType",
    "PaymentStatus",
    "PlanStatus",
    "RefreshToken",
    "Server",
    "ServerGroup",
    "ServerStatus",
    "SubscriptionStatus",
    "User",
    "UserDevice",
    "UserStatus",
]
