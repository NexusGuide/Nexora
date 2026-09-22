"""FastAPI dependencies: current user, admin role checks, client metadata."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AccountInactiveError,
    PermissionDeniedError,
    TokenInvalidError,
)
from app.core.logging import user_id_ctx
from app.core.security import TokenError, decode_token
from app.db.base import get_session
from app.models.enums import AdminRole, UserStatus
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def client_ip(request: Request) -> str | None:
    """Client IP, trusting X-Forwarded-For only behind the reverse proxy.

    Nginx sets this header and strips any value the client supplied, so the
    first entry is the real client address.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    return (request.headers.get("User-Agent") or "")[:255] or None


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise TokenInvalidError("Authorization header is missing")

    try:
        claims = decode_token(credentials.credentials, "access", settings=settings)
    except TokenError as exc:
        raise TokenInvalidError(str(exc)) from exc

    user = await session.get(User, claims["sub"])
    if user is None:
        raise TokenInvalidError("Token subject no longer exists")
    if user.status is not UserStatus.ACTIVE:
        raise AccountInactiveError(
            f"This account is {user.status.value.lower()}",
            details={"status": user.status.value},
        )

    user_id_ctx.set(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: AdminRole) -> Callable[[User], User]:
    """Dependency factory for RBAC-protected admin endpoints (spec rule 31)."""

    allowed = set(roles)

    def _guard(user: CurrentUser) -> User:
        if user.admin_role is None:
            raise PermissionDeniedError("Admin access required")
        # OWNER is always permitted, regardless of the listed roles.
        if AdminRole(user.admin_role) is AdminRole.OWNER:
            return user
        if AdminRole(user.admin_role) not in allowed:
            raise PermissionDeniedError(
                "Your role does not permit this action",
                details={"required": sorted(r.value for r in allowed)},
            )
        return user

    return _guard
