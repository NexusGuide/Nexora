"""Panel and audit-log models.

Panel credentials are the most sensitive data the platform stores. They are
written only as ciphertext produced by :class:`~app.core.security.CredentialCipher`,
never in plaintext columns, never in the repository, and never in the APK.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.db.types import EnumString
from app.models.enums import PanelStatus, PanelType, ServerStatus


class Panel(UUIDMixin, TimestampMixin, Base):
    """An Xray panel the backend manages users on.

    Only the ``encrypted_*`` columns hold credentials. There is deliberately no
    plaintext column for them, so an accidental ``SELECT *`` in a log or a
    database dump yields ciphertext rather than working logins.
    """

    __tablename__ = "panels"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    panel_type: Mapped[PanelType] = mapped_column(EnumString(PanelType, 16), index=True)
    base_url: Mapped[str] = mapped_column(String(512))

    # --- encrypted at rest -------------------------------------------------
    encrypted_username: Mapped[str | None] = mapped_column(Text)
    encrypted_password: Mapped[str | None] = mapped_column(Text)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)

    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[PanelStatus] = mapped_column(
        EnumString(PanelStatus, 16), default=PanelStatus.ACTIVE, index=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    # JSON list of panel group ids every new user is placed in. On a
    # group-based panel this is what grants inbound access; without it a
    # provisioned user exists but has no link. Not a credential, so plain.
    default_group_ids: Mapped[str | None] = mapped_column(Text)

    servers: Mapped[list[Server]] = relationship(
        back_populates="panel", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def group_id_list(self) -> list[int]:
        if not self.default_group_ids:
            return []
        try:
            return [int(g) for g in json.loads(self.default_group_ids)]
        except (ValueError, TypeError):
            return []

    @property
    def has_credentials(self) -> bool:
        """What API responses expose instead of the credentials themselves."""
        return bool(self.encrypted_password or self.encrypted_api_key)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Panel id={self.id} name={self.name} type={self.panel_type}>"


class ServerGroup(UUIDMixin, TimestampMixin, Base):
    """A region grouping shown to users (Germany, Netherlands, ...)."""

    __tablename__ = "server_groups"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    country_code: Mapped[str | None] = mapped_column(String(2))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    servers: Mapped[list[Server]] = relationship(back_populates="group", lazy="selectin")


class Server(UUIDMixin, TimestampMixin, Base):
    """A node exposed to users, belonging to a panel and a region group."""

    __tablename__ = "servers"

    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("server_groups.id", ondelete="SET NULL"), index=True
    )
    panel_id: Mapped[str] = mapped_column(
        ForeignKey("panels.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    region: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ServerStatus] = mapped_column(
        EnumString(ServerStatus, 16), default=ServerStatus.ACTIVE, index=True
    )
    load_percent: Mapped[int] = mapped_column(Integer, default=0)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    panel: Mapped[Panel] = relationship(back_populates="servers")
    group: Mapped[ServerGroup | None] = relationship(back_populates="servers")


class AuditLog(UUIDMixin, Base):
    """Record of a sensitive administrative action (spec rule 32).

    ``metadata_json`` is redacted before writing — it must never carry a
    credential, even for a panel-credential change.
    """

    __tablename__ = "audit_logs"

    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
