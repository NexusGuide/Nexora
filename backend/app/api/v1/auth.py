"""Authentication endpoints (spec rule 10)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import CurrentUser, SessionDep, client_ip, user_agent
from app.schemas.auth import (
    AuthResult,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenPair,
    UserPublic,
    VerifyEmailRequest,
)
from app.schemas.common import SuccessResponse
from app.services.auth_service import AuthService
from app.services.recovery_service import RecoveryService

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


# --- Account recovery -------------------------------------------------------


@router.post(
    "/forgot-password",
    summary="Request a password reset link",
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    session: SessionDep,
    ip: Annotated[str | None, Depends(client_ip)],
):
    """Send a reset link if the identifier matches an account.

    Always answers 200 with the same body, whether or not the account exists.
    Anything else would turn this endpoint into a way to test which addresses
    are registered.
    """
    await RecoveryService(session).request_password_reset(
        payload.identifier, ip_address=ip
    )
    await session.commit()
    return _envelope(
        {
            "message": (
                "If an account matches, a reset link has been sent to its email address."
            )
        },
        request,
    )


@router.post("/reset-password", summary="Set a new password using a reset link")
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    session: SessionDep,
):
    """Consume a reset token and set a new password.

    Every existing session is revoked: a reset is what someone does when they
    believe their account is compromised.
    """
    await RecoveryService(session).reset_password(payload.token, payload.new_password)
    await session.commit()
    return _envelope(
        {"message": "Password updated. Sign in again on all your devices."},
        request,
    )


@router.post("/verify", summary="Confirm an email address")
async def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    session: SessionDep,
):
    user = await RecoveryService(session).verify_email(payload.token)
    await session.commit()
    return _envelope(UserPublic.model_validate(user), request)


@router.post(
    "/resend-verification",
    summary="Resend the email verification link",
)
async def resend_verification(request: Request, session: SessionDep, user: CurrentUser):
    await RecoveryService(session).request_email_verification(user)
    await session.commit()
    return _envelope(
        {"message": "If the address needs confirming, a link was sent."}, request
    )
