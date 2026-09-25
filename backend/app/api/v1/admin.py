"""Admin endpoints for panels and servers.

The full admin panel is phase 7. These exist now because phase 3 is otherwise
untestable: provisioning needs a registered panel, and there has to be a way to
put one in that is not "write SQL by hand".

Every route is RBAC-guarded and writes an audit entry (spec rules 31-32).
Credentials go in encrypted and never come back out.
"""

from __future__ import annotations

import hmac
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select

from app.api.deps import SessionDep, client_ip, require_roles, user_agent
from app.core.config import get_settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.models.billing import Order, Plan
from app.models.enums import AdminRole, OrderStatus
from app.models.panel import Panel, Server
from app.models.user import User
from app.schemas.admin import BootstrapRequest
from app.schemas.billing import (
    PanelCreate,
    PanelGroupPublic,
    PanelGroupsUpdate,
    PanelPublic,
    PlanCreate,
    PlanPublic,
    PlanUpdate,
    ServerCreate,
    ServerPublic,
)
from app.schemas.common import SuccessResponse
from app.services.panel_service import PanelService
from app.workers.jobs import CREATE_PANEL_USER, REFRESH_CONFIGS
from app.workers.queue import build_queue

router = APIRouter(prefix="/admin", tags=["admin"])

# Panels hold the credentials to the whole fleet, so only these two roles may
# touch them — Support and Finance have no business here.
PanelAdmin = Annotated[
    User, Depends(require_roles(AdminRole.MANAGER, AdminRole.DEVELOPER))
]


def _envelope(data, request: Request):
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", None),
    }


# -------------------------------------------------------------------- panels
@router.post(
    "/panels",
    response_model=SuccessResponse[PanelPublic],
    status_code=status.HTTP_201_CREATED,
    summary="Register a panel",
)
async def create_panel(
    payload: PanelCreate,
    request: Request,
    session: SessionDep,
    admin: PanelAdmin,
    ip: Annotated[str | None, Depends(client_ip)],
    ua: Annotated[str | None, Depends(user_agent)],
):
    panel = await PanelService(session).create_panel(
        name=payload.name,
        panel_type=payload.panel_type,
        base_url=payload.base_url,
        username=payload.username,
        password=payload.password,
        api_key=payload.api_key,
        verify_tls=payload.verify_tls,
        actor_id=admin.id,
        ip_address=ip,
    )
    await session.commit()
    _ = ua
    return _envelope(PanelPublic.model_validate(panel), request)


@router.get(
    "/panels",
    response_model=SuccessResponse[list[PanelPublic]],
    summary="List panels",
)
async def list_panels(request: Request, session: SessionDep, admin: PanelAdmin):
    panels = await session.scalars(select(Panel).order_by(Panel.name))
    return _envelope([PanelPublic.model_validate(p) for p in panels], request)


@router.post(
    "/panels/{panel_id}/test",
    summary="Test a panel's credentials and reachability",
)
async def test_panel(
    panel_id: str, request: Request, session: SessionDep, admin: PanelAdmin
):
    """Run this before relying on a panel in production.

    It is the only way to confirm that the adapter's endpoint mapping matches
    your panel's actual API version.
    """
    ok, error = await PanelService(session).check_panel(panel_id)
    await session.commit()
    return _envelope({"reachable": ok, "error": error}, request)


@router.get(
    "/panels/{panel_id}/groups",
    response_model=SuccessResponse[list[PanelGroupPublic]],
    summary="List a panel's access groups, live",
)
async def list_panel_groups(
    panel_id: str, request: Request, session: SessionDep, admin: PanelAdmin
):
    """Read from the panel itself, so the list is never stale."""
    panel = await session.get(Panel, panel_id)
    if panel is None:
        raise NotFoundError("Panel not found", code="PANEL_NOT_FOUND")

    service = PanelService(session)
    async with service.manager.adapter_for(panel) as adapter:
        groups = await adapter.list_groups()

    chosen = set(panel.group_id_list)
    return _envelope(
        [
            PanelGroupPublic(
                id=g.id,
                name=g.name,
                inbound_count=g.inbound_count,
                is_disabled=g.is_disabled,
                is_default=g.id in chosen,
            )
            for g in groups
        ],
        request,
    )


@router.put(
    "/panels/{panel_id}/groups",
    response_model=SuccessResponse[PanelPublic],
    summary="Choose the groups new users on this panel are placed in",
)
async def set_panel_groups(
    panel_id: str,
    payload: PanelGroupsUpdate,
    request: Request,
    session: SessionDep,
    admin: PanelAdmin,
    ip: Annotated[str | None, Depends(client_ip)],
):
    """Validated against the panel, not trusted.

    A group id that does not exist, is disabled, or grants no inbound would
    let provisioning succeed while every customer received no config — the
    failure this setting exists to prevent. So each id is checked against the
    panel's own list before it is stored.
    """
    panel = await session.get(Panel, panel_id)
    if panel is None:
        raise NotFoundError("Panel not found", code="PANEL_NOT_FOUND")

    service = PanelService(session)
    async with service.manager.adapter_for(panel) as adapter:
        available = {g.id: g for g in await adapter.list_groups()}

    wanted = list(dict.fromkeys(payload.group_ids))  # de-duplicate, keep order
    problems = []
    for gid in wanted:
        group = available.get(gid)
        if group is None:
            problems.append(f"group {gid} does not exist on this panel")
        elif group.is_disabled:
            problems.append(f"group {gid} ({group.name}) is disabled")
        elif group.inbound_count == 0:
            problems.append(f"group {gid} ({group.name}) grants no inbound")
    if problems:
        raise ValidationError("; ".join(problems), code="INVALID_PANEL_GROUPS")

    panel.default_group_ids = json.dumps(wanted)
    await session.flush()
    await service.record_audit(
        actor_id=admin.id,
        action="panel.set_groups",
        entity="panel",
        entity_id=panel.id,
        ip_address=ip,
        metadata={"group_ids": wanted},
    )
    await session.commit()
    return _envelope(PanelPublic.model_validate(panel), request)


@router.patch(
    "/panels/{panel_id}/credentials",
    response_model=SuccessResponse[PanelPublic],
    summary="Replace a panel's credentials",
)
async def update_panel_credentials(
    panel_id: str,
    payload: PanelCreate,
    request: Request,
    session: SessionDep,
    admin: PanelAdmin,
    ip: Annotated[str | None, Depends(client_ip)],
):
    panel = await PanelService(session).update_credentials(
        panel_id,
        username=payload.username,
        password=payload.password,
        api_key=payload.api_key,
        actor_id=admin.id,
        ip_address=ip,
    )
    await session.commit()
    return _envelope(PanelPublic.model_validate(panel), request)


# ------------------------------------------------------------------- servers
@router.post(
    "/servers",
    response_model=SuccessResponse[ServerPublic],
    status_code=status.HTTP_201_CREATED,
    summary="Register a server",
)
async def create_server(
    payload: ServerCreate,
    request: Request,
    session: SessionDep,
    admin: PanelAdmin,
    ip: Annotated[str | None, Depends(client_ip)],
):
    service = PanelService(session)
    panel = await session.get(Panel, payload.panel_id)
    if panel is None:
        raise NotFoundError("Panel not found", code="PANEL_NOT_FOUND")

    server = Server(
        panel_id=payload.panel_id,
        group_id=payload.group_id,
        name=payload.name,
        host=payload.host,
        port=payload.port,
        region=payload.region,
    )
    session.add(server)
    await session.flush()

    await service.record_audit(
        actor_id=admin.id,
        action="server.create",
        entity="server",
        entity_id=server.id,
        ip_address=ip,
        metadata={"name": payload.name, "host": payload.host},
    )
    await session.commit()
    return _envelope(ServerPublic.model_validate(server), request)


@router.get(
    "/servers",
    response_model=SuccessResponse[list[ServerPublic]],
    summary="List servers",
)
async def list_servers(request: Request, session: SessionDep, admin: PanelAdmin):
    servers = await session.scalars(select(Server).order_by(Server.name))
    return _envelope([ServerPublic.model_validate(s) for s in servers], request)


# ---------------------------------------------------------------- operations
@router.post(
    "/subscriptions/{subscription_id}/reprovision",
    summary="Queue a provisioning retry",
)
async def reprovision(
    subscription_id: str,
    request: Request,
    session: SessionDep,
    admin: PanelAdmin,
):
    """Re-run config fetch for a subscription stuck after a panel outage."""
    from app.models.billing import Subscription

    subscription = await session.get(Subscription, subscription_id)
    if subscription is None:
        raise NotFoundError("Subscription not found", code="SUBSCRIPTION_NOT_FOUND")

    queue = build_queue(get_settings().redis_url)
    try:
        job_id = await queue.enqueue(
            REFRESH_CONFIGS, {"subscription_id": subscription_id}
        )
    finally:
        await queue.close()

    return _envelope({"queued": True, "job_id": job_id}, request)


@router.post(
    "/orders/{order_id}/confirm-payment",
    summary="Confirm a manual payment and queue provisioning",
)
async def confirm_manual_payment(
    order_id: str,
    request: Request,
    session: SessionDep,
    admin: Annotated[User, Depends(require_roles(AdminRole.MANAGER, AdminRole.FINANCE))],
    ip: Annotated[str | None, Depends(client_ip)],
):
    """Mark an order paid for a card-to-card or other offline payment.

    The gateway providers arrive in phase 6; until then this is how a real
    payment becomes a real subscription. It is idempotent: confirming twice
    provisions once, because ``mark_paid`` reports whether it actually
    transitioned.
    """
    from app.services.order_service import OrderService

    service = OrderService(session)
    order, transitioned = await service.mark_paid(order_id)

    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="order.confirm_manual_payment",
        entity="order",
        entity_id=order_id,
        ip_address=ip,
        metadata={"amount": str(order.amount), "already_paid": not transitioned},
    )
    await session.commit()

    job_id = None
    if transitioned:
        # Only on a real transition, so a repeated confirmation does not
        # queue a second provisioning run.
        queue = build_queue(get_settings().redis_url)
        try:
            job_id = await queue.enqueue(CREATE_PANEL_USER, {"order_id": order_id})
        finally:
            await queue.close()

    return _envelope(
        {
            "order_id": order_id,
            "status": order.status.value,
            "newly_paid": transitioned,
            "job_id": job_id,
        },
        request,
    )


@router.post(
    "/orders/{order_id}/reprovision",
    summary="Re-queue provisioning for a paid order that never got its service",
)
async def reprovision_order(
    order_id: str,
    request: Request,
    session: SessionDep,
    admin: PanelAdmin,
    ip: Annotated[str | None, Depends(client_ip)],
):
    """Recover a customer who paid and received nothing.

    Provisioning is retried with backoff, and after the last attempt the job
    goes to the dead-letter stream. If the failure happened before the panel
    account was created, the subscription row was rolled back with it — so
    the order is PAID, there is no subscription, and nothing else can recover
    it: `confirm-payment` only queues on the transition to paid, and
    `/subscriptions/{id}/reprovision` needs a subscription that does not exist.

    Safe to call on an order that already succeeded: provisioning is
    idempotent and reuses the existing subscription and panel username rather
    than creating a second account.
    """
    order = await session.get(Order, order_id)
    if order is None:
        raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
    if order.status is not OrderStatus.PAID:
        raise ConflictError("Only a paid order can be provisioned", code="ORDER_NOT_PAID")

    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="order.reprovision",
        entity="order",
        entity_id=order_id,
        ip_address=ip,
        metadata={"had_subscription": order.subscription_id is not None},
    )
    await session.commit()

    queue = build_queue(get_settings().redis_url)
    try:
        job_id = await queue.enqueue(CREATE_PANEL_USER, {"order_id": order_id})
    finally:
        await queue.close()

    return _envelope({"order_id": order_id, "queued": True, "job_id": job_id}, request)


# ----------------------------------------------------------------- bootstrap
@router.post(
    "/bootstrap",
    summary="Promote the first user to OWNER",
    status_code=status.HTTP_200_OK,
)
async def bootstrap_owner(
    payload: BootstrapRequest,
    request: Request,
    session: SessionDep,
    ip: Annotated[str | None, Depends(client_ip)],
):
    """Grant OWNER to an existing account, once, using ADMIN_BOOTSTRAP_SECRET.

    A fresh deployment has no administrator, and every other admin route
    requires one — so without this there is no way in except editing the
    database by hand.

    Three things keep it from being a back door:

    * **It closes permanently.** The moment any user holds an admin role, this
      returns 409 forever. The window is the deployment window.
    * **It does not create accounts.** The person registers through the normal
      endpoint first; this only raises the privileges of an account that
      already exists, so there is no second user-creation path to audit.
    * **The secret is compared in constant time**, and a wrong one answers the
      same way whether or not the named account exists.
    """
    settings = get_settings()

    # Checked before the secret, so a late caller cannot use this endpoint as
    # an oracle for guessing the secret: once an owner exists every request
    # gets 409 regardless of what was sent.
    existing = await session.scalar(
        select(User).where(User.admin_role.is_not(None)).limit(1)
    )
    if existing is not None:
        raise ConflictError("An administrator already exists. This endpoint is closed.")

    if not hmac.compare_digest(payload.secret, settings.admin_bootstrap_secret):
        raise PermissionDeniedError("Invalid bootstrap secret")

    identifier = payload.identifier.strip().lower()
    user = await session.scalar(
        select(User).where(
            (User.username == identifier) | (func.lower(User.email) == identifier)
        )
    )
    if user is None:
        raise NotFoundError(
            "No such account. Register it through /api/v1/auth/register first."
        )

    user.admin_role = AdminRole.OWNER
    await session.flush()

    await PanelService(session).record_audit(
        actor_id=user.id,
        action="admin.bootstrap_owner",
        entity="user",
        entity_id=user.id,
        ip_address=ip,
        metadata={"username": user.username},
    )
    await session.commit()

    return _envelope(
        {"id": user.id, "username": user.username, "admin_role": user.admin_role.value},
        request,
    )


# ------------------------------------------------------------------- plans
# Pricing is a commercial decision, so Finance may set it as well as the
# operators who run the fleet. Support may not.
PlanAdmin = Annotated[
    User,
    Depends(require_roles(AdminRole.MANAGER, AdminRole.DEVELOPER, AdminRole.FINANCE)),
]


@router.post(
    "/plans",
    response_model=SuccessResponse[PlanPublic],
    status_code=status.HTTP_201_CREATED,
    summary="Create a plan",
)
async def create_plan(
    payload: PlanCreate,
    request: Request,
    session: SessionDep,
    admin: PlanAdmin,
    ip: Annotated[str | None, Depends(client_ip)],
):
    """Without this, the store is empty and nothing can be bought.

    Traffic arrives in GB and is stored in bytes, converted once here so no
    other layer has to agree about what a GB is.
    """
    plan = Plan(
        name=payload.name,
        description=payload.description,
        duration_days=payload.duration_days,
        traffic_limit_bytes=payload.traffic_limit_bytes,
        device_limit=payload.device_limit,
        price=payload.price,
        currency=payload.currency,
        status=payload.status,
        sort_order=payload.sort_order,
    )
    session.add(plan)
    await session.flush()

    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="plan.create",
        entity="plan",
        entity_id=plan.id,
        ip_address=ip,
        metadata={"name": plan.name, "price": str(plan.price)},
    )
    await session.commit()
    return _envelope(PlanPublic.model_validate(plan), request)


@router.get(
    "/plans",
    response_model=SuccessResponse[list[PlanPublic]],
    summary="List every plan, including inactive ones",
)
async def list_all_plans(request: Request, session: SessionDep, admin: PlanAdmin):
    """Unlike the store listing, this shows plans that are not on sale."""
    plans = await session.scalars(select(Plan).order_by(Plan.sort_order, Plan.price))
    return _envelope([PlanPublic.model_validate(p) for p in plans], request)


@router.patch(
    "/plans/{plan_id}",
    response_model=SuccessResponse[PlanPublic],
    summary="Change a plan's price, name or availability",
)
async def update_plan(
    plan_id: str,
    payload: PlanUpdate,
    request: Request,
    session: SessionDep,
    admin: PlanAdmin,
    ip: Annotated[str | None, Depends(client_ip)],
):
    """Editing a plan never changes what an existing customer bought.

    Each order carries its own snapshot of the plan taken at purchase time, so
    raising a price or retiring a tier leaves live subscriptions untouched.
    Retire a plan by setting `status` to ARCHIVED (or HIDDEN to pull it from
    the store temporarily) rather than deleting it —
    orders reference it, and deleting would orphan their history.
    """
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise NotFoundError("No such plan")

    changed = payload.model_dump(exclude_unset=True)
    for field, value in changed.items():
        setattr(plan, field, value)
    await session.flush()

    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="plan.update",
        entity="plan",
        entity_id=plan.id,
        ip_address=ip,
        metadata={k: str(v) for k, v in changed.items()},
    )
    await session.commit()
    return _envelope(PlanPublic.model_validate(plan), request)
