"""User, device, refresh-token and login-attempt models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.db.types import EnumString
from app.models.enums import AdminRole, DeviceStatus, UserStatus


class User(UUIDMixin, TimestampMixin, Base):
    """An end user of the VPN service.

    ``password_hash`` holds an Argon2id hash. The plain password is never
    stored, logged or returned by any endpoint.
    """

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(
        EnumString(UserStatus, 16), default=UserStatus.ACTIVE, index=True
    )
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Admin access. A NULL role means "no admin access at all".
    admin_role: Mapped[AdminRole | None] = mapped_column(
        EnumString(AdminRole, 16), default=None
    )

    devices: Mapped[list[UserDevice]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.admin_role is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Deliberately omits every credential field.
        return f"<User id={self.id} username={self.username} status={self.status}>"


class UserDevice(UUIDMixin, TimestampMixin, Base):
    """A device bound to a user, for per-plan device limits."""

    __tablename__ = "user_devices"
    __table_args__ = (
        Index("ix_user_devices_user_device", "user_id", "device_id", unique=True),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[str] = mapped_column(String(128))
    device_name: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(32), default="android")
    app_version: Mapped[str | None] = mapped_column(String(32))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[DeviceStatus] = mapped_column(
        EnumString(DeviceStatus, 16), default=DeviceStatus.ACTIVE
    )

    user: Mapped[User] = relationship(back_populates="devices")


class RefreshToken(UUIDMixin, TimestampMixin, Base):
    """A refresh token, stored as a SHA-256 hash.

    Rotation model: every use issues a new token and marks the old one used,
    linking both to the same ``family_id``. Presenting an already-used token
    means the token leaked, so the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_family_active", "family_id", "revoked_at"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    jti: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    device_id: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(45))

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and self.revoked_at is None


class LoginAttempt(UUIDMixin, Base):
    """Login attempt record, backing account and IP lockout."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_identifier_time", "identifier", "attempted_at"),
        Index("ix_login_attempts_ip_time", "ip_address", "attempted_at"),
    )

    identifier: Mapped[str] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    successful: Mapped[bool] = mapped_column(Boolean, default=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_agent: Mapped[str | None] = mapped_column(Text)


class VerificationToken(UUIDMixin, Base):
    """A single-use token for password reset or address verification.

    Stored as a SHA-256 hash, like refresh tokens: a database leak must not
    hand over the ability to take over accounts. The raw token exists only in
    the email that was sent.
    """

    __tablename__ = "verification_tokens"
    __table_args__ = (Index("ix_verification_tokens_user_purpose", "user_id", "purpose"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))

    @property
    def is_usable(self) -> bool:
        return self.used_at is None
