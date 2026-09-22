"""SQLAlchemy models.

Importing this package registers every table on ``Base.metadata`` — which is
what Alembic autogenerate relies on.
"""

from app.models.billing import (
    UNLIMITED_TRAFFIC,
    Config,
    Order,
    Payment,
    Plan,
    PlanServer,
    Subscription,
)
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
from app.models.user import (
    LoginAttempt,
    RefreshToken,
    User,
    UserDevice,
    VerificationToken,
)

__all__ = [
    "UNLIMITED_TRAFFIC",
    "AdminRole",
    "AuditLog",
    "Config",
    "DeviceStatus",
    "LoginAttempt",
    "Order",
    "OrderStatus",
    "Panel",
    "PanelStatus",
    "PanelType",
    "Payment",
    "PaymentStatus",
    "Plan",
    "PlanServer",
    "PlanStatus",
    "RefreshToken",
    "Server",
    "ServerGroup",
    "ServerStatus",
    "Subscription",
    "SubscriptionStatus",
    "User",
    "UserDevice",
    "UserStatus",
    "VerificationToken",
]
