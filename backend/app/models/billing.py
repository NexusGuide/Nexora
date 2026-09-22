"""Plans, orders, payments, subscriptions and configs.

The money-touching invariants live here as database constraints, not as
application checks alone: a unique index is enforced by the database even when
two requests race, while an ``if not exists`` in Python is not.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.db.types import EnumString
from app.models.enums import (
    OrderStatus,
    PaymentStatus,
    PlanStatus,
    SubscriptionStatus,
)

# Traffic is stored in bytes as BIGINT. A plan of 1 TB is 1.1e12, which
# overflows a 32-bit INTEGER — hence BigInteger everywhere traffic appears.
UNLIMITED_TRAFFIC = 0


class Plan(UUIDMixin, TimestampMixin, Base):
    """A purchasable service tier."""

    __tablename__ = "plans"

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)

    duration_days: Mapped[int] = mapped_column(Integer)
    traffic_limit_bytes: Mapped[int] = mapped_column(
        BigInteger, default=UNLIMITED_TRAFFIC
    )
    device_limit: Mapped[int] = mapped_column(Integer, default=1)

    # Numeric, never float: binary floating point cannot represent decimal
    # currency exactly, and rounding drift in money is not acceptable.
    price: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="IRT")

    status: Mapped[PlanStatus] = mapped_column(
        EnumString(PlanStatus, 16), default=PlanStatus.ACTIVE, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    servers: Mapped[list[PlanServer]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_unlimited_traffic(self) -> bool:
        return self.traffic_limit_bytes == UNLIMITED_TRAFFIC

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Plan id={self.id} name={self.name} status={self.status}>"


class PlanServer(UUIDMixin, Base):
    """Which servers a plan may provision on."""

    __tablename__ = "plan_servers"
    __table_args__ = (UniqueConstraint("plan_id", "server_id", name="plan_server"),)

    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[str] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), index=True
    )

    plan: Mapped[Plan] = relationship(back_populates="servers")


class Order(UUIDMixin, TimestampMixin, Base):
    """A purchase intent.

    ``idempotency_key`` carries a UNIQUE constraint. That is what makes
    "a successful payment must never create the same service twice" (spec rule
    19) true under concurrency: a duplicate submission loses the insert race at
    the database level and is served the original order instead.
    """

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_user_status", "user_id", "status"),
        Index("ix_orders_created_at", "created_at"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), index=True
    )
    # Renewals attach to the subscription being extended; new purchases do not.
    subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )

    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="IRT")

    status: Mapped[OrderStatus] = mapped_column(
        EnumString(OrderStatus, 16), default=OrderStatus.PENDING, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # A snapshot of the plan at purchase time. Without it, editing a plan's
    # price or quota would retroactively rewrite what past customers bought.
    plan_snapshot: Mapped[str] = mapped_column(Text)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(255))

    payments: Mapped[list[Payment]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OrderStatus.PAID,
            OrderStatus.FAILED,
            OrderStatus.CANCELLED,
            OrderStatus.REFUNDED,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Order id={self.id} status={self.status} amount={self.amount}>"


class Payment(UUIDMixin, TimestampMixin, Base):
    """A payment attempt against an order.

    An order may have several: a failed gateway attempt followed by a
    successful one. ``transaction_id`` is unique so a replayed gateway callback
    cannot be verified twice.
    """

    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_order_status", "order_id", "status"),)

    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)

    # Set once the gateway issues its reference. Unique, so a duplicate
    # callback for the same transaction is rejected by the database.
    transaction_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True
    )
    authority: Mapped[str | None] = mapped_column(String(128), index=True)

    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="IRT")

    status: Mapped[PaymentStatus] = mapped_column(
        EnumString(PaymentStatus, 16), default=PaymentStatus.CREATED, index=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(255))

    # The gateway's response, minus anything secret. Never the API key.
    provider_response: Mapped[str | None] = mapped_column(Text)

    order: Mapped[Order] = relationship(back_populates="payments")


class Subscription(UUIDMixin, TimestampMixin, Base):
    """A provisioned service: the link between a user and a panel account."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_subscriptions_user_status", "user_id", "status"),
        Index("ix_subscriptions_expire_at", "expire_at"),
        # One panel account per panel, so a retried provisioning job cannot
        # attach a second subscription to the same panel user.
        UniqueConstraint("panel_id", "panel_username", name="panel_user"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), index=True
    )
    panel_id: Mapped[str | None] = mapped_column(
        ForeignKey("panels.id", ondelete="SET NULL"), index=True
    )

    # Identity on the panel. Null until provisioning succeeds.
    panel_username: Mapped[str | None] = mapped_column(String(128))
    panel_user_id: Mapped[str | None] = mapped_column(String(128))

    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    traffic_limit_bytes: Mapped[int] = mapped_column(
        BigInteger, default=UNLIMITED_TRAFFIC
    )
    traffic_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    device_limit: Mapped[int] = mapped_column(Integer, default=1)

    subscription_url: Mapped[str | None] = mapped_column(Text)

    status: Mapped[SubscriptionStatus] = mapped_column(
        EnumString(SubscriptionStatus, 16),
        default=SubscriptionStatus.PENDING,
        index=True,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provisioning_error: Mapped[str | None] = mapped_column(Text)

    configs: Mapped[list[Config]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_unlimited_traffic(self) -> bool:
        return self.traffic_limit_bytes == UNLIMITED_TRAFFIC

    @property
    def traffic_remaining_bytes(self) -> int | None:
        """Remaining bytes, or ``None`` when the plan is unlimited."""
        if self.is_unlimited_traffic:
            return None
        return max(0, self.traffic_limit_bytes - self.traffic_used_bytes)

    @property
    def is_traffic_exhausted(self) -> bool:
        if self.is_unlimited_traffic:
            return False
        return self.traffic_used_bytes >= self.traffic_limit_bytes

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Subscription id={self.id} status={self.status}>"


class Config(UUIDMixin, TimestampMixin, Base):
    """A connection config belonging to a subscription.

    ``config_data`` is a connection URI, not a credential of the platform, but
    it does grant access to the user's own service — so it is returned only to
    its owner and is redacted from logs alongside `subscription_url`.
    """

    __tablename__ = "configs"
    __table_args__ = (
        Index("ix_configs_subscription_active", "subscription_id", "is_active"),
    )

    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[str | None] = mapped_column(
        ForeignKey("servers.id", ondelete="SET NULL"), index=True
    )

    name: Mapped[str] = mapped_column(String(128))
    protocol: Mapped[str | None] = mapped_column(String(32))
    host: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    config_data: Mapped[str] = mapped_column(Text)

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    subscription: Mapped[Subscription] = relationship(back_populates="configs")
