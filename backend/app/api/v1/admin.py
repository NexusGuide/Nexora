"""Admin endpoints for panels and servers.

The full admin panel is phase 7. These exist now because phase 3 is otherwise
untestable: provisioning needs a registered panel, and there has to be a way to
put one in that is not "write SQL by hand".

Every route is RBAC-guarded and writes an audit entry (spec rules 31-32).
Credentials go in encrypted and never come back out.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select

from app.api.deps import SessionDep, client_ip, require_roles, user_agent
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.enums import AdminRole
from app.models.panel import Panel, Server
from app.models.user import User
from app.schemas.billing import (
    PanelCreate,
    PanelPublic,
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
