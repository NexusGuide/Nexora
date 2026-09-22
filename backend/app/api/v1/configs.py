"""Config endpoints (spec rule 28).

A config URI grants access to the user's own service, so every query here is
joined through `subscriptions.user_id`. There is no code path that returns a
config without proving ownership first.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser, SessionDep
from app.schemas.billing import ConfigPublic
from app.schemas.common import SuccessResponse
from app.services.config_service import ConfigService

router = APIRouter(prefix="/configs", tags=["configs"])


def _envelope(data, request: Request):
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", None),
    }


@router.get(
    "",
    response_model=SuccessResponse[list[ConfigPublic]],
    summary="List your configs",
)
async def list_configs(
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    subscription_id: Annotated[str | None, Query(max_length=36)] = None,
):
    configs = await ConfigService(session).list_for_user(
        user.id, subscription_id=subscription_id
    )
    return _envelope([ConfigPublic.model_validate(c) for c in configs], request)


@router.get(
    "/{config_id}",
    response_model=SuccessResponse[ConfigPublic],
    summary="Config detail, including the connection URI",
)
async def get_config(
    config_id: str, request: Request, session: SessionDep, user: CurrentUser
):
    config = await ConfigService(session).get_for_user(config_id, user.id)
    return _envelope(ConfigPublic.model_validate(config), request)


@router.post(
    "/{config_id}/activate",
    response_model=SuccessResponse[ConfigPublic],
    summary="Make this the active config",
)
async def activate_config(
    config_id: str, request: Request, session: SessionDep, user: CurrentUser
):
    config = await ConfigService(session).activate(config_id, user.id)
    await session.commit()
    return _envelope(ConfigPublic.model_validate(config), request)


@router.delete("/{config_id}", summary="Hide a config from your list")
async def delete_config(
    config_id: str, request: Request, session: SessionDep, user: CurrentUser
):
    # Hides it locally. The next refresh restores it if the panel still issues
    # it — this is not a way to revoke access to a server.
    await ConfigService(session).delete(config_id, user.id)
    await session.commit()
    return _envelope({"deleted": True, "config_id": config_id}, request)
