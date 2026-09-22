"""Authentication endpoints (spec rule 10)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import CurrentUser, SessionDep, client_ip, user_agent
from app.core.exceptions import NotImplementedYetError
from app.schemas.auth import (
    AuthResult,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserPublic,
)
from app.schemas.common import SuccessResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _envelope(data, request: Request):
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", None),
    }


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[UserPublic],
    summary="Create an account",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: SessionDep,
):
    service = AuthService(session)
    user = await service.register(payload)
    await session.commit()
    return _envelope(UserPublic.model_validate(user), request)


@router.post(
    "/login",
    response_model=SuccessResponse[AuthResult],
    summary="Exchange credentials for a token pair",
)
async def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
    ip: Annotated[str | None, Depends(client_ip)],
    ua: Annotated[str | None, Depends(user_agent)],
):
    service = AuthService(session)
    result = await service.login(payload, ip_address=ip, user_agent=ua)
    await session.commit()
    return _envelope(result, request)


@router.post(
    "/refresh",
    response_model=SuccessResponse[TokenPair],
    summary="Rotate a refresh token",
)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: SessionDep,
    ip: Annotated[str | None, Depends(client_ip)],
    ua: Annotated[str | None, Depends(user_agent)],
):
    service = AuthService(session)
    tokens = await service.refresh(payload.refresh_token, ip_address=ip, user_agent=ua)
    await session.commit()
    return _envelope(tokens, request)


@router.post("/logout", summary="Revoke the current session or all sessions")
async def logout(
    payload: LogoutRequest,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
):
    service = AuthService(session)
    await service.logout(
        user.id,
        refresh_token=payload.refresh_token,
        all_devices=payload.all_devices,
    )
    await session.commit()
    return _envelope({"revoked": True}, request)


@router.get(
    "/me",
    response_model=SuccessResponse[UserPublic],
    summary="The authenticated user",
)
async def me(request: Request, user: CurrentUser):
    return _envelope(UserPublic.model_validate(user), request)


# --- Declared by the spec, not implemented in phase 1 ------------------------
# These exist as the correct interface with an explicit 501, rather than a
# fake success (spec rule 67). Implementing them needs an email/SMS transport,
# which phase 1 does not configure.


@router.post("/verify", summary="Verify an email or phone (not implemented)")
async def verify() -> None:
    raise NotImplementedYetError(
        "Account verification needs an email/SMS transport, planned for phase 6."
    )


@router.post("/forgot-password", summary="Request a reset link (not implemented)")
async def forgot_password() -> None:
    raise NotImplementedYetError(
        "Password reset needs an email/SMS transport, planned for phase 6."
    )


@router.post("/reset-password", summary="Reset a password (not implemented)")
async def reset_password() -> None:
    raise NotImplementedYetError(
        "Password reset needs an email/SMS transport, planned for phase 6."
    )
