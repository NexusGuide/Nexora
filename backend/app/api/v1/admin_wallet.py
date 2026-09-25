"""Admin side of payments: the review queue, wallet corrections, settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.admin_roles import roles_for
from app.api.deps import SessionDep, client_ip, require_roles
from app.api.queue import enqueue
from app.core.exceptions import NotFoundError
from app.models.enums import TopUpStatus
from app.models.user import User
from app.models.wallet import TopUp, WalletTransaction
from app.schemas.wallet import PaymentSettings, TopUpApprove, TopUpReject, WalletAdjust
from app.services.panel_service import PanelService
from app.services.wallet_service import WalletService
from app.workers.jobs import CREATE_PANEL_USER

router = APIRouter(prefix="/admin", tags=["admin-payments"])

PaymentsAdmin = Annotated[User, Depends(require_roles(*roles_for("payments")))]
SettingsAdmin = Annotated[User, Depends(require_roles(*roles_for("payments.settings")))]
Ip = Annotated[str | None, Depends(client_ip)]


def _envelope(data, request: Request):
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", None),
    }


def _topup_row(t: TopUp, username: str | None, balance) -> dict:
    return {
        "id": t.id,
        "user_id": t.user_id,
        "username": username,
        "wallet_balance": str(balance) if balance is not None else None,
        "method": t.method.value,
        "status": t.status.value,
        "amount": str(t.amount),
        "currency": t.currency,
        "reference": t.reference,
        "payer_note": t.payer_note,
        "destination": t.destination,
        "network": t.network,
        "asset": t.asset,
        "crypto_amount": str(t.crypto_amount) if t.crypto_amount is not None else None,
        "rate": str(t.rate) if t.rate is not None else None,
        "order_id": t.order_id,
        "credited_amount": str(t.credited_amount)
        if t.credited_amount is not None
        else None,
        "reject_reason": t.reject_reason,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "reviewed_at": t.reviewed_at.isoformat() if t.reviewed_at else None,
    }


# ------------------------------------------------------------------ settings
@router.get("/payment-settings", summary="Card and crypto details shown to customers")
async def get_payment_settings(
    request: Request, session: SessionDep, admin: SettingsAdmin
):
    settings = await WalletService(session).get_settings()
    return _envelope(settings.model_dump(mode="json"), request)


@router.put("/payment-settings", summary="Replace the payment settings")
async def put_payment_settings(
    payload: PaymentSettings,
    request: Request,
    session: SessionDep,
    admin: SettingsAdmin,
    ip: Ip,
):
    saved = await WalletService(session).save_settings(payload, admin.id)
    # The full new settings go in the audit log: if the card number is ever
    # changed behind the owner's back, this is where it shows.
    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="payments.settings_update",
        entity="app_setting",
        entity_id="payments",
        ip_address=ip,
        metadata=saved.model_dump(mode="json"),
    )
    await session.commit()
    return _envelope(saved.model_dump(mode="json"), request)


# ------------------------------------------------------------------- top-ups
@router.get("/topups", summary="Top-up requests, pending first")
async def list_topups(
    request: Request,
    session: SessionDep,
    admin: PaymentsAdmin,
    topup_status: Annotated[TopUpStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    stmt = select(TopUp, User.username, User.wallet_balance).join(
        User, User.id == TopUp.user_id
    )
    count_stmt = select(func.count()).select_from(TopUp)
    if topup_status is not None:
        stmt = stmt.where(TopUp.status == topup_status)
        count_stmt = count_stmt.where(TopUp.status == topup_status)
    rows = (
        await session.execute(
            stmt.order_by(
                TopUp.created_at.asc()
                if topup_status is TopUpStatus.PENDING
                else TopUp.created_at.desc()
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()

    # Flag a reference that appears on more than one request: the same
    # receipt filed twice, possibly from two accounts.
    refs = [t.reference.lower() for t, _, _ in rows]
    dup_counts: dict[str, int] = {}
    if refs:
        for ref, n in (
            await session.execute(
                select(func.lower(TopUp.reference), func.count())
                .where(func.lower(TopUp.reference).in_(refs))
                .group_by(func.lower(TopUp.reference))
            )
        ).all():
            dup_counts[ref] = n

    items = []
    for t, username, balance in rows:
        row = _topup_row(t, username, balance)
        row["reference_seen"] = dup_counts.get(t.reference.lower(), 1)
        items.append(row)
    return _envelope(
        {"items": items, "total": await session.scalar(count_stmt) or 0}, request
    )


@router.post(
    "/topups/{topup_id}/approve", summary="Credit the wallet for a checked payment"
)
async def approve_topup(
    topup_id: str,
    request: Request,
    session: SessionDep,
    admin: PaymentsAdmin,
    ip: Ip,
    payload: TopUpApprove | None = None,
):
    amount = payload.amount if payload else None
    service = WalletService(session)
    topup, paid_order = await service.approve_topup(topup_id, admin.id, amount)
    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="topup.approve",
        entity="topup",
        entity_id=topup_id,
        ip_address=ip,
        metadata={
            "claimed": str(topup.amount),
            "credited": str(topup.credited_amount),
            "paid_order": paid_order.id if paid_order else None,
        },
    )
    await session.commit()
    if paid_order is not None:
        await enqueue(CREATE_PANEL_USER, {"order_id": paid_order.id})

    user = await session.get(User, topup.user_id)
    return _envelope(
        {
            **_topup_row(
                topup,
                user.username if user else None,
                user.wallet_balance if user else None,
            ),
            "paid_order_id": paid_order.id if paid_order else None,
        },
        request,
    )


@router.post("/topups/{topup_id}/reject", summary="Reject a payment that did not arrive")
async def reject_topup(
    topup_id: str,
    payload: TopUpReject,
    request: Request,
    session: SessionDep,
    admin: PaymentsAdmin,
    ip: Ip,
):
    topup = await WalletService(session).reject_topup(topup_id, admin.id, payload.reason)
    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="topup.reject",
        entity="topup",
        entity_id=topup_id,
        ip_address=ip,
        metadata={"reason": topup.reject_reason, "amount": str(topup.amount)},
    )
    await session.commit()
    user = await session.get(User, topup.user_id)
    return _envelope(
        _topup_row(
            topup, user.username if user else None, user.wallet_balance if user else None
        ),
        request,
    )


# ------------------------------------------------------------------- wallets
@router.get("/users/{user_id}/wallet", summary="A customer's balance and ledger")
async def user_wallet(
    user_id: str, request: Request, session: SessionDep, admin: PaymentsAdmin
):
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="USER_NOT_FOUND")
    rows = await session.scalars(
        select(WalletTransaction)
        .where(WalletTransaction.user_id == user_id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(50)
    )
    return _envelope(
        {
            "user_id": user.id,
            "username": user.username,
            "balance": str(user.wallet_balance),
            "transactions": [
                {
                    "id": t.id,
                    "kind": t.kind.value,
                    "amount": str(t.amount),
                    "balance_after": str(t.balance_after),
                    "order_id": t.order_id,
                    "topup_id": t.topup_id,
                    "actor_id": t.actor_id,
                    "note": t.note,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in rows
            ],
        },
        request,
    )


@router.post("/users/{user_id}/wallet/adjust", summary="Credit or debit a wallet by hand")
async def adjust_wallet(
    user_id: str,
    payload: WalletAdjust,
    request: Request,
    session: SessionDep,
    admin: PaymentsAdmin,
    ip: Ip,
):
    tx = await WalletService(session).adjust(
        user_id, payload.amount, payload.note, admin.id
    )
    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="wallet.adjust",
        entity="user",
        entity_id=user_id,
        ip_address=ip,
        metadata={
            "amount": str(payload.amount),
            "note": tx.note,
            "balance_after": str(tx.balance_after),
        },
    )
    await session.commit()
    return _envelope({"balance": str(tx.balance_after), "transaction_id": tx.id}, request)
