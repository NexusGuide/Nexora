"""Admin-only request contracts."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models.enums import AdminRole, UserStatus


class BootstrapRequest(BaseModel):
    """Claim OWNER on a fresh deployment.

    `secret` is `ADMIN_BOOTSTRAP_SECRET` from the server's environment, and
    `identifier` is the username or email of an account that has already
    registered normally.
    """

    secret: str = Field(..., min_length=32, max_length=512)
    identifier: str = Field(..., min_length=1, max_length=255)


class UserAdminUpdate(BaseModel):
    """What an administrator may change about an account.

    Both fields are optional; only the ones present in the request body are
    applied. ``admin_role`` is special: sending ``null`` explicitly removes a
    role, while leaving the field out changes nothing — the difference is read
    from ``model_fields_set``, not from the value.

    PENDING is not accepted as a target status: it is the state of an account
    that has not finished signing up, not something an operator assigns.
    """

    status: UserStatus | None = None
    admin_role: AdminRole | None = None

    @field_validator("status")
    @classmethod
    def _no_pending(cls, v: UserStatus | None) -> UserStatus | None:
        if v is UserStatus.PENDING:
            raise ValueError("status must be ACTIVE, SUSPENDED or BANNED")
        return v
