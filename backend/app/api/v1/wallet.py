"""Customer wallet: balance, top-ups, payment methods, paying an order."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, SessionDep
from app.api.queue import enqueue
from app.schemas.billing import OrderPublic
from app.schemas.common import SuccessResponse
from app.schemas.wallet import (
    PaymentMethodsPublic,
    TopUpCreate,
    TopUpPublic,
    WalletPublic,
    WalletTransactionPublic,
)
from app.services.wallet_service import WALLET_CURRENCY, WalletService
from app.workers.jobs import CREATE_PANEL_USER

router = APIRouter(tags=["wallet"])


def _envelope(data, request: Request):
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", None),
    }


@router.get(
    "/payment-methods",
    response_model=SuccessResponse[PaymentMethodsPublic],
    summary="Where to send money for a top-up",
)
async def payment_methods(request: Request, session: SessionDep, user: CurrentUser):
    # Signed-in only: the card number and holder's name are for customers,
    # not for anyone who finds the API.
    return _envelope(await WalletService(session).public_methods(), request)


@router.get(
    "/wallet",
    response_model=SuccessResponse[WalletPublic],
    summary="Balance and recent wallet activity",
)
async def get_wallet(request: Request, session: SessionDep, user: CurrentUser):
    service = WalletService(session)
    data = WalletPublic(
        balance=await service.balance(user.id),
        currency=WALLET_CURRENCY,
        pending_topups=await service.pending_count(user.id),
        transactions=[
            WalletTransactionPublic.model_validate(t)
            for t in await service.transactions(user.id)
        ],
    )
    return _envelope(data, request)


@router.get(
    "/wallet/topups",
    response_model=SuccessResponse[list[TopUpPublic]],
    summary="Your top-up requests",
)
async def list_topups(request: Request, session: SessionDep, user: CurrentUser):
    rows = await WalletService(session).topups(user.id)
    return _envelope([TopUpPublic.model_validate(t) for t in rows], request)


@router.post(
    "/wallet/topups",
    response_model=SuccessResponse[TopUpPublic],
    status_code=status.HTTP_201_CREATED,
    summary="Report a payment for review",
)
async def create_topup(
    payload: TopUpCreate, request: Request, session: SessionDep, user: CurrentUser
):
    """File a card-to-card or crypto payment. An admin reviews it; the wallet
    is credited only on approval."""
    topup = await WalletService(session).create_topup(user.id, payload)
    await session.commit()
    return _envelope(TopUpPublic.model_validate(topup), request)


@router.post(
    "/wallet/topups/{topup_id}/cancel",
    response_model=SuccessResponse[TopUpPublic],
    summary="Withdraw a top-up that has not been reviewed",
)
async def cancel_topup(
    topup_id: str, request: Request, session: SessionDep, user: CurrentUser
):
    topup = await WalletService(session).cancel_topup(topup_id, user.id)
    await session.commit()
    return _envelope(TopUpPublic.model_validate(topup), request)


@router.post(
    "/orders/{order_id}/pay",
    response_model=SuccessResponse[OrderPublic],
    summary="Pay an order from the wallet",
)
async def pay_order(
    order_id: str, request: Request, session: SessionDep, user: CurrentUser
):
    """Charge the wallet and queue the service. Idempotent: paying an order
    that is already paid charges nothing and queues nothing."""
    order, newly_paid = await WalletService(session).pay_order(order_id, user.id)
    await session.commit()
    if newly_paid:
        await enqueue(CREATE_PANEL_USER, {"order_id": order.id})
    return _envelope(OrderPublic.model_validate(order), request)
