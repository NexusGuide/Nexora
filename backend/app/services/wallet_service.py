"""The wallet: top-ups, purchases and adjustments.

Invariants, each enforced where it cannot be bypassed:

* A balance never goes negative — a CHECK constraint on ``users``.
* A top-up is credited at most once and an order charged at most once — the
  UNIQUE ``idempotency_key`` on ``wallet_transactions``.
* Balance and ledger agree — both are written in the same transaction, with
  the user's row locked, and nothing else writes the balance.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import ROUND_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.billing import Order, Payment
from app.models.enums import (
    OrderStatus,
    PaymentStatus,
    TopUpMethod,
    TopUpStatus,
    WalletTxKind,
)
from app.models.user import User
from app.models.wallet import AppSetting, TopUp, WalletTransaction
from app.schemas.wallet import (
    CardMethodPublic,
    CryptoWalletPublic,
    PaymentMethodsPublic,
    PaymentSettings,
    TopUpCreate,
)
from app.services.order_service import OrderService

logger = logging.getLogger(__name__)

PAYMENT_SETTINGS_KEY = "payments"
WALLET_CURRENCY = "IRT"
# Enough for a customer who paid twice, not enough to flood the review queue.
MAX_PENDING_TOPUPS = 3


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------- settings
    async def get_settings(self) -> PaymentSettings:
        row = await self.session.get(AppSetting, PAYMENT_SETTINGS_KEY)
        if row is None:
            return PaymentSettings()
        return PaymentSettings.model_validate_json(row.value)

    async def save_settings(
        self, settings: PaymentSettings, actor_id: str
    ) -> PaymentSettings:
        row = await self.session.get(AppSetting, PAYMENT_SETTINGS_KEY)
        value = settings.model_dump_json()
        if row is None:
            row = AppSetting(key=PAYMENT_SETTINGS_KEY, value=value)
            self.session.add(row)
        row.value = value
        row.updated_by = actor_id
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return settings

    async def public_methods(self) -> PaymentMethodsPublic:
        s = await self.get_settings()
        card = None
        if s.card.enabled and s.card.number:
            card = CardMethodPublic(
                number=s.card.number,
                holder=s.card.holder,
                bank=s.card.bank,
                instructions=s.card.instructions,
            )
        crypto = (
            [CryptoWalletPublic(**w.model_dump()) for w in s.crypto.wallets]
            if s.crypto.enabled
            else []
        )
        return PaymentMethodsPublic(
            currency=s.currency,
            min_topup=s.min_topup,
            max_topup=s.max_topup,
            card=card,
            crypto=crypto,
            crypto_instructions=s.crypto.instructions if crypto else "",
        )

    # ----------------------------------------------------------------- read
    async def balance(self, user_id: str) -> Decimal:
        value = await self.session.scalar(
            select(User.wallet_balance).where(User.id == user_id)
        )
        if value is None:
            raise NotFoundError("User not found", code="USER_NOT_FOUND")
        return Decimal(value)

    async def transactions(
        self, user_id: str, limit: int = 30
    ) -> list[WalletTransaction]:
        rows = await self.session.scalars(
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user_id)
            .order_by(WalletTransaction.created_at.desc())
            .limit(limit)
        )
        return list(rows)

    async def topups(self, user_id: str, limit: int = 20) -> list[TopUp]:
        rows = await self.session.scalars(
            select(TopUp)
            .where(TopUp.user_id == user_id)
            .order_by(TopUp.created_at.desc())
            .limit(limit)
        )
        return list(rows)

    async def pending_count(self, user_id: str) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(TopUp)
                .where(TopUp.user_id == user_id, TopUp.status == TopUpStatus.PENDING)
            )
            or 0
        )

    # ------------------------------------------------------------- top-ups
    async def create_topup(self, user_id: str, payload: TopUpCreate) -> TopUp:
        settings = await self.get_settings()
        amount = payload.amount

        if amount < settings.min_topup or amount > settings.max_topup:
            raise ValidationError(
                f"The amount must be between {settings.min_topup:,.0f} and "
                f"{settings.max_topup:,.0f}",
                code="TOPUP_AMOUNT_OUT_OF_RANGE",
            )

        if await self.pending_count(user_id) >= MAX_PENDING_TOPUPS:
            raise ConflictError(
                "You have top-ups still waiting for review. Wait for them first.",
                code="TOO_MANY_PENDING_TOPUPS",
            )

        # The same receipt filed twice would be credited twice by an admin
        # who reviews them minutes apart. A rejected or cancelled one may be
        # filed again: that is how a typo gets corrected.
        reused = await self.session.scalar(
            select(TopUp.id).where(
                TopUp.method == payload.method,
                func.lower(TopUp.reference) == payload.reference.lower(),
                TopUp.status.in_([TopUpStatus.PENDING, TopUpStatus.APPROVED]),
            )
        )
        if reused is not None:
            raise ConflictError(
                "This receipt has already been submitted",
                code="TOPUP_REFERENCE_USED",
            )

        if payload.order_id is not None:
            order = await self.session.get(Order, payload.order_id)
            if order is None or order.user_id != user_id:
                raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")

        topup = TopUp(
            user_id=user_id,
            method=payload.method,
            status=TopUpStatus.PENDING,
            amount=amount,
            currency=WALLET_CURRENCY,
            reference=payload.reference,
            payer_note=(payload.payer_note or "").strip() or None,
            order_id=payload.order_id,
        )

        if payload.method is TopUpMethod.CARD:
            if not settings.card.enabled or not settings.card.number:
                raise ValidationError(
                    "Card payments are not available", code="PAYMENT_METHOD_DISABLED"
                )
            topup.destination = settings.card.number
        else:
            network = (payload.network or "").upper()
            asset = (payload.asset or "USDT").upper()
            wallet = next(
                (
                    w
                    for w in settings.crypto.wallets
                    if w.network == network and w.asset == asset
                ),
                None,
            )
            if not settings.crypto.enabled or wallet is None:
                raise ValidationError(
                    "That crypto network is not available", code="PAYMENT_METHOD_DISABLED"
                )
            topup.destination = wallet.address
            topup.network = wallet.network
            topup.asset = wallet.asset
            topup.rate = wallet.rate
            # Rounded up to 6 places: the customer is told to send at least
            # this much, never a fraction short of the amount they claim.
            topup.crypto_amount = (amount / wallet.rate).quantize(
                Decimal("0.000001"), rounding=ROUND_UP
            )

        self.session.add(topup)
        await self.session.flush()
        logger.info(
            "topup_created",
            extra={"extra_fields": {"topup_id": topup.id, "method": topup.method.value}},
        )
        return topup

    async def cancel_topup(self, topup_id: str, user_id: str) -> TopUp:
        topup = await self._lock_topup(topup_id)
        if topup.user_id != user_id:
            raise NotFoundError("Top-up not found", code="TOPUP_NOT_FOUND")
        if topup.status is not TopUpStatus.PENDING:
            raise ConflictError(
                "Only a pending top-up can be cancelled", code="TOPUP_NOT_PENDING"
            )
        topup.status = TopUpStatus.CANCELLED
        topup.reviewed_at = datetime.now(UTC)
        await self.session.flush()
        return topup

    async def approve_topup(
        self, topup_id: str, admin_id: str, amount: Decimal | None = None
    ) -> tuple[TopUp, Order | None]:
        """Credit the wallet and, when the top-up was for an order, pay it.

        Returns ``(topup, paid_order)``. ``paid_order`` is the order that was
        newly paid here, so the caller can queue its provisioning; it is
        ``None`` when there was no order, it was no longer payable, or the
        balance still does not cover it.
        """
        topup = await self._lock_topup(topup_id)
        if topup.status is TopUpStatus.APPROVED:
            return topup, None  # a repeated click: already credited
        if topup.status is not TopUpStatus.PENDING:
            raise ConflictError(
                f"A {topup.status.value.lower()} top-up cannot be approved",
                code="TOPUP_NOT_PENDING",
            )

        credit = amount if amount is not None else Decimal(topup.amount)
        await self._apply(
            user_id=topup.user_id,
            amount=credit,
            kind=WalletTxKind.TOPUP,
            key=f"topup:{topup.id}",
            topup_id=topup.id,
            actor_id=admin_id,
        )
        topup.status = TopUpStatus.APPROVED
        topup.credited_amount = credit
        topup.reviewed_by = admin_id
        topup.reviewed_at = datetime.now(UTC)
        await self.session.flush()

        paid = None
        if topup.order_id is not None:
            order = await self.session.get(Order, topup.order_id)
            if (
                order is not None
                and order.status is OrderStatus.PENDING
                and await self.balance(topup.user_id) >= Decimal(order.amount)
            ):
                paid, transitioned = await self.pay_order(order.id, topup.user_id)
                if not transitioned:
                    paid = None
        return topup, paid

    async def reject_topup(self, topup_id: str, admin_id: str, reason: str) -> TopUp:
        topup = await self._lock_topup(topup_id)
        if topup.status is not TopUpStatus.PENDING:
            raise ConflictError(
                f"A {topup.status.value.lower()} top-up cannot be rejected",
                code="TOPUP_NOT_PENDING",
            )
        topup.status = TopUpStatus.REJECTED
        topup.reject_reason = reason.strip()[:255]
        topup.reviewed_by = admin_id
        topup.reviewed_at = datetime.now(UTC)
        await self.session.flush()
        return topup

    # ------------------------------------------------------------ purchase
    async def pay_order(self, order_id: str, user_id: str) -> tuple[Order, bool]:
        """Pay an order from the wallet. Returns ``(order, newly_paid)``.

        Debit first, then mark paid, in one transaction: if marking paid
        fails, the debit rolls back with it. A second call finds the order
        PAID and the ledger key taken, and charges nothing.
        """
        orders = OrderService(self.session)
        order = await orders._lock_order(order_id, user_id)

        if order.status is OrderStatus.PAID:
            return order, False
        if order.status is not OrderStatus.PENDING:
            raise ConflictError(
                f"An order that is {order.status.value.lower()} cannot be paid",
                code="ORDER_NOT_PAYABLE",
            )
        if order.currency != WALLET_CURRENCY:
            raise ValidationError(
                "This order is not in the wallet's currency", code="CURRENCY_MISMATCH"
            )

        price = Decimal(order.amount)
        balance = await self.balance(user_id)
        if balance < price:
            raise ValidationError(
                "Your wallet balance is not enough for this order",
                code="INSUFFICIENT_BALANCE",
                details={
                    "balance": str(balance),
                    "required": str(price),
                    "missing": str(price - balance),
                },
            )

        await self._apply(
            user_id=user_id,
            amount=-price,
            kind=WalletTxKind.PURCHASE,
            key=f"order:{order.id}",
            order_id=order.id,
        )
        payment = Payment(
            order_id=order.id,
            provider="wallet",
            amount=price,
            currency=order.currency,
            status=PaymentStatus.PENDING,
            provider_response=json.dumps({"source": "wallet"}),
        )
        self.session.add(payment)
        await self.session.flush()

        order, transitioned = await orders.mark_paid(order.id, payment_id=payment.id)
        return order, transitioned

    # ---------------------------------------------------------- adjustment
    async def adjust(
        self, user_id: str, amount: Decimal, note: str, admin_id: str
    ) -> WalletTransaction:
        """An admin's manual correction: a refund, compensation, a mistake."""
        return await self._apply(
            user_id=user_id,
            amount=amount,
            kind=WalletTxKind.ADJUSTMENT,
            key=f"adjust:{uuid.uuid4().hex}",
            actor_id=admin_id,
            note=note.strip()[:255],
        )

    # ------------------------------------------------------------ internals
    async def _apply(
        self,
        *,
        user_id: str,
        amount: Decimal,
        kind: WalletTxKind,
        key: str,
        topup_id: str | None = None,
        order_id: str | None = None,
        actor_id: str | None = None,
        note: str | None = None,
    ) -> WalletTransaction:
        # populate_existing: a User already in this session's identity map
        # would otherwise keep the balance it was loaded with, and the new
        # balance would be computed from a stale figure.
        user = await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise NotFoundError("User not found", code="USER_NOT_FOUND")

        already = await self.session.scalar(
            select(WalletTransaction).where(WalletTransaction.idempotency_key == key)
        )
        if already is not None:
            raise ConflictError(
                "This change was already applied", code="WALLET_TX_DUPLICATE"
            )

        new_balance = Decimal(user.wallet_balance) + amount
        if new_balance < 0:
            raise ValidationError(
                "The wallet balance cannot go below zero",
                code="INSUFFICIENT_BALANCE",
                details={"balance": str(user.wallet_balance), "required": str(-amount)},
            )

        tx = WalletTransaction(
            user_id=user_id,
            kind=kind,
            amount=amount,
            balance_after=new_balance,
            currency=WALLET_CURRENCY,
            idempotency_key=key,
            topup_id=topup_id,
            order_id=order_id,
            actor_id=actor_id,
            note=note,
        )
        user.wallet_balance = new_balance
        self.session.add(tx)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Lost a race on the idempotency key (or the CHECK caught an
            # overdraft another transaction caused). Nothing was applied.
            await self.session.rollback()
            raise ConflictError(
                "This change was already applied", code="WALLET_TX_DUPLICATE"
            ) from exc

        logger.info(
            "wallet_changed",
            extra={
                "extra_fields": {
                    "user_id": user_id,
                    "kind": kind.value,
                    "amount": str(amount),
                }
            },
        )
        return tx

    async def _lock_topup(self, topup_id: str) -> TopUp:
        topup = await self.session.scalar(
            select(TopUp)
            .where(TopUp.id == topup_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if topup is None:
            raise NotFoundError("Top-up not found", code="TOPUP_NOT_FOUND")
        return topup


__all__ = ["MAX_PENDING_TOPUPS", "PAYMENT_SETTINGS_KEY", "WalletService"]
