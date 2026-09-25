"""User profile and device endpoints (spec rules 12, 28)."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep
from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import DeviceStatus
from app.models.user import User, UserDevice
from app.schemas.auth import UserPublic
from app.schemas.billing import DevicePublic, UserUpdate
from app.schemas.common import SuccessResponse
from app.services.device_service import DeviceService

router = APIRouter(tags=["users"])


def _envelope(data, request: Request):
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", None),
    }


@router.get("/me", response_model=SuccessResponse[UserPublic], summary="Your profile")
async def get_me(request: Request, user: CurrentUser):
    return _envelope(UserPublic.model_validate(user), request)


@router.patch(
    "/me", response_model=SuccessResponse[UserPublic], summary="Update your profile"
)
async def update_me(
    payload: UserUpdate,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
):
    if payload.email is None and payload.phone is None:
        return _envelope(UserPublic.model_validate(user), request)

    clauses = []
    if payload.email is not None:
        clauses.append(func.lower(User.email) == payload.email)
    if payload.phone is not None:
        clauses.append(User.phone == payload.phone)

    clash = await session.scalar(select(User).where(or_(*clauses), User.id != user.id))
    if clash is not None:
        raise ConflictError(
            "Those contact details are already in use",
            code="CONTACT_IN_USE",
        )

    if payload.email is not None and payload.email != user.email:
        user.email = payload.email
        # A changed address is unverified until proven, or it could be used to
        # claim somebody else's account through password reset.
        user.is_email_verified = False
    if payload.phone is not None and payload.phone != user.phone:
        user.phone = payload.phone
        user.is_phone_verified = False

    await session.commit()
    return _envelope(UserPublic.model_validate(user), request)


@router.get(
    "/me/devices",
    response_model=SuccessResponse[list[DevicePublic]],
    summary="Your registered devices",
)
async def list_devices(request: Request, session: SessionDep, user: CurrentUser):
    devices = await session.scalars(
        select(UserDevice)
        .where(
            UserDevice.user_id == user.id,
            UserDevice.status == DeviceStatus.ACTIVE,
        )
        .order_by(UserDevice.last_seen_at.desc().nullslast())
    )
    return _envelope([DevicePublic.model_validate(d) for d in devices], request)


@router.delete(
    "/me/devices/{device_id}",
    status_code=status.HTTP_200_OK,
    summary="Revoke a device",
)
async def revoke_device(
    device_id: str, request: Request, session: SessionDep, user: CurrentUser
):
    # Revoked rather than deleted: the row is evidence of where an account has
    # been used, which matters when a user reports a compromise.
    # The service also ends the sessions bound to that device.
    device = await DeviceService(session).revoke(user.id, device_id)
    if device is None:
        raise NotFoundError("Device not found", code="DEVICE_NOT_FOUND")
    await session.commit()
    return _envelope({"revoked": True, "device_id": device_id}, request)
