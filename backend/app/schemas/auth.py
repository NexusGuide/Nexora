"""Authentication request/response schemas.

No response model here carries a password, a password hash or a panel
credential — that is enforced by which fields exist, not by remembering to
strip them.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import UserStatus

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
MIN_PASSWORD_LENGTH = 8


def _validate_password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(value) > 128:
        raise ValueError("Password must be at most 128 characters.")
    checks = (
        (re.search(r"[a-z]", value), "a lowercase letter"),
        (re.search(r"[A-Z]", value), "an uppercase letter"),
        (re.search(r"[0-9]", value), "a digit"),
    )
    missing = [label for ok, label in checks if not ok]
    if missing:
        raise ValueError("Password must contain " + ", ".join(missing) + ".")
    return value


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)
    email: EmailStr | None = None
    phone: str | None = None

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        if not USERNAME_RE.match(v):
            raise ValueError(
                "Username may contain only letters, digits and underscore "
                "(3-32 characters)."
            )
        return v.lower()

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, v: EmailStr | None) -> str | None:
        # Addresses are matched case-insensitively at sign-in. Stored as typed,
        # "Mehdi@gmail.com" could never sign in as "mehdi@gmail.com" — and a
        # phone keyboard capitalises the first letter on its own.
        return str(v).strip().lower() if v else None

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str | None) -> str | None:
        if v and not PHONE_RE.match(v):
            raise ValueError("Phone must be 10-15 digits, optionally led by '+'.")
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password(v)


class LoginRequest(BaseModel):
    """``identifier`` accepts a username, an email or a phone number."""

    identifier: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)
    device_id: str | None = Field(default=None, max_length=128)
    device_name: str | None = Field(default=None, max_length=128)
    app_version: str | None = Field(default=None, max_length=32)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=16)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None
    all_devices: bool = False


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password(v)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth scheme name, not a secret
    expires_in: int = Field(..., description="Access token lifetime in seconds")


class UserPublic(BaseModel):
    """The only user representation ever returned to a client."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: EmailStr | None = None
    phone: str | None = None
    status: UserStatus
    is_email_verified: bool
    created_at: datetime
    last_login_at: datetime | None = None


class AuthResult(BaseModel):
    user: UserPublic
    tokens: TokenPair


class ForgotPasswordRequest(BaseModel):
    """Accepts a username, email or phone — whatever the user remembers."""

    identifier: str = Field(..., min_length=3, max_length=255)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=256)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password(v)


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=256)
