"""Plans, orders and subscriptions (spec rule 28)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.exceptions import NotFoundError, NotImplementedYetError
from app.models.billing import Plan
from app.models.enums import PlanStatus, SubscriptionStatus
from app.schemas.billing import (
    OrderCreate,
    OrderPublic,
    PlanPublic,
    SubscriptionPublic,
)
from app.schemas.common import SuccessResponse
from app.services.order_service import OrderService
from app.services.subscription_service import SubscriptionService

router = APIRouter(tags=["store"])


def _envelope(data, request: Request):
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", None),
    }


# --------------------------------------------------------------------- plans
@router.get(
    "/plans",
    response_model=SuccessResponse[list[PlanPublic]],
    summary="List purchasable plans",
)
async def list_plans(request: Request, session: SessionDep):
    # Only ACTIVE plans are listed: HIDDEN and ARCHIVED exist so an operator
    # can retire a plan without breaking the orders that reference it.
    plans = await session.scalars(
        select(Plan)
        .where(Plan.status == PlanStatus.ACTIVE)
        .order_by(Plan.sort_order, Plan.price)
    )
    return _envelope([PlanPublic.model_validate(p) for p in plans], request)


@router.get(
    "/plans/{plan_id}",
    response_model=SuccessResponse[PlanPublic],
    summary="Plan detail",
)
async def get_plan(plan_id: str, request: Request, session: SessionDep):
    plan = await session.get(Plan, plan_id)
    if plan is None or plan.status is PlanStatus.ARCHIVED:
        raise NotFoundError("Plan not found", code="PLAN_NOT_FOUND")
    return _envelope(PlanPublic.model_validate(plan), request)


# -------------------------------------------------------------------- orders
@router.post(
    "/orders",
    response_model=SuccessResponse[OrderPublic],
    summary="Create an order",
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    payload: OrderCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
):
    """Create an order for a plan.

    Idempotent: repeating the request with the same `idempotency_key` returns
    the original order with 200 instead of creating a second one.
    """
    service = OrderService(session)
    order, created = await service.create_order(
        user_id=user.id,
        plan_id=payload.plan_id,
        client_idempotency_key=payload.idempotency_key,
        subscription_id=payload.subscription_id,
    )
    await session.commit()

    if not created:
        response.status_code = status.HTTP_200_OK
    return _envelope(OrderPublic.model_validate(order), request)


@router.get(
    "/orders",
    response_model=SuccessResponse[list[OrderPublic]],
    summary="List your orders",
)
async def list_orders(
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    orders = await OrderService(session).list_orders(user.id, limit=limit, offset=offset)
    return _envelope([OrderPublic.model_validate(o) for o in orders], request)


@router.get(
    "/orders/{order_id}",
    response_model=SuccessResponse[OrderPublic],
    summary="Order detail",
)
async def get_order(
    order_id: str, request: Request, session: SessionDep, user: CurrentUser
):
    order = await OrderService(session).get_order(order_id, user.id)
    return _envelope(OrderPublic.model_validate(order), request)


@router.post(
    "/orders/{order_id}/cancel",
    response_model=SuccessResponse[OrderPublic],
    summary="Cancel a pending order",
)
async def cancel_order(
    order_id: str, request: Request, session: SessionDep, user: CurrentUser
):
    order = await OrderService(session).cancel_order(order_id, user.id)
    await session.commit()
    return _envelope(OrderPublic.model_validate(order), request)


# ------------------------------------------------------------- subscriptions
@router.get(
    "/subscriptions",
    response_model=SuccessResponse[list[SubscriptionPublic]],
    summary="List your subscriptions",
)
async def list_subscriptions(
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    subscription_status: Annotated[SubscriptionStatus | None, Query()] = None,
):
    subs = await SubscriptionService(session).list_for_user(
        user.id, status=subscription_status
    )
    return _envelope([SubscriptionPublic.model_validate(s) for s in subs], request)


@router.get(
    "/subscriptions/{subscription_id}",
    response_model=SuccessResponse[SubscriptionPublic],
    summary="Subscription detail",
)
async def get_subscription(
    subscription_id: str,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
):
    sub = await SubscriptionService(session).get_for_user(subscription_id, user.id)
    return _envelope(SubscriptionPublic.model_validate(sub), request)


@router.post(
    "/subscriptions/{subscription_id}/renew",
    response_model=SuccessResponse[OrderPublic],
    summary="Order a renewal",
    status_code=status.HTTP_201_CREATED,
)
async def renew_subscription(
    subscription_id: str,
    payload: OrderCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
):
    """Create a renewal order for a subscription.

    Renewing is buying again, so it produces an order to be paid. The
    subscription itself is only extended once that order is paid.
    """
    await SubscriptionService(session).get_for_user(subscription_id, user.id)

    order, created = await OrderService(session).create_order(
        user_id=user.id,
        plan_id=payload.plan_id,
        client_idempotency_key=payload.idempotency_key,
        subscription_id=subscription_id,
    )
    await session.commit()

    if not created:
        response.status_code = status.HTTP_200_OK
    return _envelope(OrderPublic.model_validate(order), request)


@router.post(
    "/subscriptions/{subscription_id}/refresh",
    summary="Re-fetch configs from the panel (not implemented)",
)
async def refresh_subscription(
    subscription_id: str, session: SessionDep, user: CurrentUser
) -> None:
    # The interface is correct and reachable; the panel round-trip lands with
    # the config system in phase 3 (spec rule 67 — no pretend success).
    await SubscriptionService(session).get_for_user(subscription_id, user.id)
    raise NotImplementedYetError(
        "Config refresh needs the panel provisioning worker, planned for phase 3."
    )


@router.get("/configs", summary="List your configs (not implemented)")
async def list_configs(user: CurrentUser) -> None:
    raise NotImplementedYetError(
        "Configs are delivered by the panel provisioning worker, planned for phase 3."
    )
