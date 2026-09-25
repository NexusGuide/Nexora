"""Wallet ledger, top-up requests and the operator's payment settings.

Money moves in two steps. A customer sends money outside the app (card to
card, or crypto) and files a :class:`TopUp` with the bank's tracking number or
the transaction hash. An admin checks it against the bank statement or the
chain and approves it, which credits the wallet. Orders are then paid from the
wallet.

Every change to a balance writes a :class:`WalletTransaction` whose
``idempotency_key`` is UNIQUE. That is what makes "credit this top-up" and
"charge this order" happen at most once, even when an admin double-clicks
Approve or two requests race: the second insert fails in the database.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.db.types import EnumString
from app.models.enums import TopUpMethod, TopUpStatus, WalletTxKind


class TopUp(UUIDMixin, TimestampMixin, Base):
    """A customer's claim that they sent money, waiting for an admin."""

    __tablename__ = "wallet_topups"
    __table_args__ = (
        Index("ix_wallet_topups_status_created", "status", "created_at"),
        Index("ix_wallet_topups_method_reference", "method", "reference"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[TopUpMethod] = mapped_column(EnumString(TopUpMethod, 16))
    status: Mapped[TopUpStatus] = mapped_column(
        EnumString(TopUpStatus, 16), default=TopUpStatus.PENDING, index=True
    )

    # What the customer says they paid, in the wallet currency.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="IRT")

    # The bank's tracking number for a card transfer, or the transaction hash
    # for crypto. This is what the admin looks up.
    reference: Mapped[str] = mapped_column(String(128))
    # Free text from the customer, e.g. the last four digits of their card.
    payer_note: Mapped[str | None] = mapped_column(String(255))

    # What the customer was shown when they paid, captured at request time.
    # The operator may change the card or the rate later; the review must be
    # against what this customer actually saw.
    destination: Mapped[str] = mapped_column(String(255))
    network: Mapped[str | None] = mapped_column(String(16))
    asset: Mapped[str | None] = mapped_column(String(16))
    crypto_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    # When set, the order is paid from the wallet as soon as this top-up is
    # approved — the customer tapped "Buy", topped up, and should not have to
    # come back and tap again.
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )

    reviewed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The amount actually credited. Usually ``amount``; an admin may credit
    # what really arrived when the customer typed the wrong figure.
    credited_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    reject_reason: Mapped[str | None] = mapped_column(String(255))


class WalletTransaction(UUIDMixin, TimestampMixin, Base):
    """One change to one wallet. Append-only: rows are never edited."""

    __tablename__ = "wallet_transactions"
    __table_args__ = (Index("ix_wallet_tx_user_created", "user_id", "created_at"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[WalletTxKind] = mapped_column(EnumString(WalletTxKind, 16))
    # Positive credits, negative debits.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="IRT")

    # "topup:<id>", "order:<id>" or "adjust:<uuid>". UNIQUE: the database, not
    # an if-statement, guarantees a top-up is credited once and an order is
    # charged once.
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)

    topup_id: Mapped[str | None] = mapped_column(
        ForeignKey("wallet_topups.id", ondelete="SET NULL"), index=True
    )
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(String(255))


class AppSetting(TimestampMixin, Base):
    """Operator-editable settings, one JSON document per key.

    Holds what customers are shown — the card number to transfer to, the
    crypto addresses — which is public by nature but must still come from the
    running system, not from the repository or the APK.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
