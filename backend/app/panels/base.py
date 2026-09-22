"""The panel abstraction (spec rule 15).

The backend calls ``PanelAdapter`` methods only. No business-logic module
imports PasarGuard, X-UI or Marzban directly, so adding a panel means adding
an adapter, never editing the subscription or order flow.

Credentials arrive here already decrypted, held only for the duration of a
call. An adapter must never log, persist or return them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class PanelCredentials:
    """Decrypted credentials for a single panel, held in memory only."""

    base_url: str
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    verify_tls: bool = True
    timeout_seconds: int = 20

    def __repr__(self) -> str:
        # Overridden so a stray repr() in a log or traceback cannot leak them.
        return f"PanelCredentials(base_url={self.base_url!r}, credentials=[REDACTED])"

    __str__ = __repr__


@dataclass(slots=True)
class PanelUser:
    """A user as the panel represents it, normalised across panel types."""

    username: str
    status: str
    traffic_limit_bytes: int = 0  # 0 means unlimited
    traffic_used_bytes: int = 0
    expire_at: datetime | None = None
    subscription_url: str | None = None
    configs: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PanelUsage:
    username: str
    used_bytes: int
    limit_bytes: int
    measured_at: datetime


class PanelAdapter(ABC):
    """Contract every panel integration implements."""

    panel_type: str = "BASE"

    def __init__(self, credentials: PanelCredentials) -> None:
        self.credentials = credentials

    @abstractmethod
    async def test_connection(self) -> bool:
        """Authenticate against the panel. True when reachable and accepted."""

    @abstractmethod
    async def create_user(
        self,
        username: str,
        *,
        traffic_limit_bytes: int,
        expire_at: datetime | None,
        device_limit: int | None = None,
        inbound_tags: list[str] | None = None,
    ) -> PanelUser:
        """Create a panel user. Must be idempotent for an existing username."""

    @abstractmethod
    async def get_user(self, username: str) -> PanelUser | None: ...

    @abstractmethod
    async def update_user(self, username: str, **changes: Any) -> PanelUser: ...

    @abstractmethod
    async def delete_user(self, username: str) -> bool: ...

    @abstractmethod
    async def disable_user(self, username: str) -> bool: ...

    @abstractmethod
    async def enable_user(self, username: str) -> bool: ...

    @abstractmethod
    async def get_usage(self, username: str) -> PanelUsage: ...

    @abstractmethod
    async def get_configs(self, username: str) -> list[str]:
        """Return config URIs (vless://, vmess://, ...) for this user."""

    @abstractmethod
    async def renew_user(
        self,
        username: str,
        *,
        traffic_limit_bytes: int,
        expire_at: datetime | None,
        reset_usage: bool = True,
    ) -> PanelUser: ...

    async def close(self) -> None:
        """Release any transport held by the adapter."""
        return None

    async def __aenter__(self) -> PanelAdapter:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
