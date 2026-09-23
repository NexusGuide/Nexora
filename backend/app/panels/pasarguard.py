"""PasarGuard adapter (spec rule 16).

PasarGuard exposes a Marzban-compatible REST API: an admin token endpoint plus
CRUD under ``/api/user``. The endpoint paths and payload keys are collected in
``ENDPOINTS`` and ``FIELDS`` below so they can be adjusted for a specific panel
version without touching the logic.

VERIFY BEFORE PRODUCTION USE: the paths below match the documented
Marzban-compatible surface, but they have not been exercised against a live
PasarGuard instance in this repository. Run the connection test from the admin
panel (Panels -> Test) against your own installation first; if a call fails,
correct the mapping here rather than in calling code.

Credentials are supplied by the caller already decrypted and are never logged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.exceptions import PanelError
from app.panels.base import (
    PanelAdapter,
    PanelCredentials,
    PanelGroup,
    PanelUsage,
    PanelUser,
)
from app.services.config_parser import decode_subscription

logger = logging.getLogger(__name__)

ENDPOINTS = {
    "token": "/api/admin/token",
    "groups": "/api/groups",
    "user": "/api/user/{username}",
    "users": "/api/users",
    "user_create": "/api/user",
    "user_reset": "/api/user/{username}/reset",
}

FIELDS = {
    "used_traffic": "used_traffic",
    "data_limit": "data_limit",
    "expire": "expire",
    "status": "status",
    "subscription_url": "subscription_url",
    "links": "links",
}


class PasarGuardAdapter(PanelAdapter):
    panel_type = "PASARGUARD"

    def __init__(self, credentials: PanelCredentials) -> None:
        super().__init__(credentials)
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None

    # ------------------------------------------------------------- transport
    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.credentials.base_url.rstrip("/"),
                timeout=self.credentials.timeout_seconds,
                verify=self.credentials.verify_tls,
                follow_redirects=False,
            )
        return self._client

    async def _authenticate(self) -> str:
        """Obtain an admin token. Cached for the lifetime of the adapter."""
        if self._token:
            return self._token

        if self.credentials.api_key:
            self._token = self.credentials.api_key
            return self._token

        if not (self.credentials.username and self.credentials.password):
            raise PanelError(
                "Panel has no usable credentials configured",
                code="PANEL_NO_CREDENTIALS",
            )

        client = await self._http()
        try:
            response = await client.post(
                ENDPOINTS["token"],
                data={
                    "username": self.credentials.username,
                    "password": self.credentials.password,
                    "grant_type": "password",
                },
            )
        except httpx.RequestError as exc:
            # str(exc) carries the URL but never the body, so no credential leak.
            raise PanelError("Panel is unreachable", code="PANEL_UNREACHABLE") from exc

        if response.status_code == 401:
            raise PanelError(
                "Panel rejected the stored credentials",
                code="PANEL_AUTH_FAILED",
                status_code=502,
            )
        if response.status_code >= 400:
            raise PanelError(
                f"Panel returned HTTP {response.status_code} on authentication",
                code="PANEL_AUTH_FAILED",
            )

        token = response.json().get("access_token")
        if not token:
            raise PanelError(
                "Panel response contained no access token",
                code="PANEL_AUTH_FAILED",
            )
        self._token = token
        return token

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = await self._http()
        token = await self._authenticate()
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}

        response = await client.request(method, path, headers=headers, **kwargs)

        # A cached token can expire; re-authenticate once before giving up.
        if response.status_code == 401:
            self._token = None
            token = await self._authenticate()
            headers["Authorization"] = f"Bearer {token}"
            response = await client.request(method, path, headers=headers, **kwargs)
        return response

    # ------------------------------------------------------------- mapping
    @staticmethod
    def _to_panel_user(payload: dict[str, Any]) -> PanelUser:
        expire_raw = payload.get(FIELDS["expire"])
        expire_at: datetime | None = None
        if isinstance(expire_raw, (int, float)) and expire_raw > 0:
            expire_at = datetime.fromtimestamp(expire_raw, tz=UTC)
        elif isinstance(expire_raw, str) and expire_raw:
            try:
                expire_at = datetime.fromisoformat(expire_raw.replace("Z", "+00:00"))
            except ValueError:
                expire_at = None

        return PanelUser(
            username=payload.get("username", ""),
            status=str(payload.get(FIELDS["status"], "unknown")),
            traffic_limit_bytes=int(payload.get(FIELDS["data_limit"]) or 0),
            traffic_used_bytes=int(payload.get(FIELDS["used_traffic"]) or 0),
            expire_at=expire_at,
            subscription_url=payload.get(FIELDS["subscription_url"]),
            configs=list(payload.get(FIELDS["links"]) or []),
            raw=payload,
        )

    # -------------------------------------------------------------- contract
    async def test_connection(self) -> bool:
        await self._authenticate()
        response = await self._request("GET", ENDPOINTS["users"], params={"limit": 1})
        if response.status_code >= 400:
            raise PanelError(
                f"Panel returned HTTP {response.status_code}",
                code="PANEL_UNREACHABLE",
            )
        return True

    async def create_user(
        self,
        username: str,
        *,
        traffic_limit_bytes: int,
        expire_at: datetime | None,
        device_limit: int | None = None,
        inbound_tags: list[str] | None = None,
        group_ids: list[int] | None = None,
    ) -> PanelUser:
        existing = await self.get_user(username)
        if existing is not None:
            # Idempotent: a retried job must not create a duplicate account.
            return existing

        body: dict[str, Any] = {
            "username": username,
            FIELDS["data_limit"]: traffic_limit_bytes,
            FIELDS["expire"]: int(expire_at.timestamp()) if expire_at else 0,
            FIELDS["status"]: "active",
            "proxies": {},
            "inbounds": {},
        }
        if inbound_tags:
            body["inbounds"] = {"vless": inbound_tags}
        if group_ids:
            # PasarGuard grants inbound access through groups. Omitting this
            # on a group-based panel creates a user who exists, is active,
            # and has no link — which is exactly what the first live purchase
            # produced.
            body["group_ids"] = list(group_ids)

        response = await self._request("POST", ENDPOINTS["user_create"], json=body)
        if response.status_code == 409:
            found = await self.get_user(username)
            if found:
                return found
        if response.status_code >= 400:
            raise PanelError(
                f"Panel refused to create the user (HTTP {response.status_code})",
                code="PANEL_CREATE_FAILED",
            )
        return self._to_panel_user(response.json())

    async def list_groups(self) -> list[PanelGroup]:
        """Groups as the panel reports them, shape verified against a live panel.

        The endpoint has been seen returning both a bare list and an object
        with a ``groups`` key across versions, so both are accepted.
        """
        response = await self._request("GET", ENDPOINTS["groups"])
        if response.status_code == 404:
            # A panel without the group model: nothing to choose from.
            return []
        if response.status_code >= 400:
            raise PanelError(
                f"Panel returned HTTP {response.status_code} for list_groups",
                code="PANEL_READ_FAILED",
            )
        body = response.json()
        items = body.get("groups", []) if isinstance(body, dict) else body
        return [
            PanelGroup(
                id=int(g["id"]),
                name=str(g.get("name", "")),
                inbound_count=len(g.get("inbound_tags") or []),
                is_disabled=bool(g.get("is_disabled", False)),
            )
            for g in items or []
            if isinstance(g, dict) and "id" in g
        ]

    async def get_user(self, username: str) -> PanelUser | None:
        response = await self._request("GET", ENDPOINTS["user"].format(username=username))
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise PanelError(
                f"Panel returned HTTP {response.status_code} for get_user",
                code="PANEL_READ_FAILED",
            )
        return self._to_panel_user(response.json())

    async def update_user(self, username: str, **changes: Any) -> PanelUser:
        response = await self._request(
            "PUT", ENDPOINTS["user"].format(username=username), json=changes
        )
        if response.status_code >= 400:
            raise PanelError(
                f"Panel refused the update (HTTP {response.status_code})",
                code="PANEL_UPDATE_FAILED",
            )
        return self._to_panel_user(response.json())

    async def delete_user(self, username: str) -> bool:
        response = await self._request(
            "DELETE", ENDPOINTS["user"].format(username=username)
        )
        if response.status_code in (200, 204, 404):
            return True
        raise PanelError(
            f"Panel refused the delete (HTTP {response.status_code})",
            code="PANEL_DELETE_FAILED",
        )

    async def disable_user(self, username: str) -> bool:
        await self.update_user(username, **{FIELDS["status"]: "disabled"})
        return True

    async def enable_user(self, username: str) -> bool:
        await self.update_user(username, **{FIELDS["status"]: "active"})
        return True

    async def get_usage(self, username: str) -> PanelUsage:
        user = await self.get_user(username)
        if user is None:
            raise PanelError("User does not exist on the panel", code="PANEL_NO_USER")
        return PanelUsage(
            username=username,
            used_bytes=user.traffic_used_bytes,
            limit_bytes=user.traffic_limit_bytes,
            measured_at=datetime.now(UTC),
        )

    async def get_configs(self, username: str) -> list[str]:
        """Config URIs for a user.

        Older panels return them inline as ``links``. PasarGuard does not: its
        user object has no ``links`` field at all, only ``subscription_url``,
        so the URIs have to be fetched from the subscription itself. Reading
        ``links`` alone returned nothing on a live panel even for a user with
        full access.
        """
        user = await self.get_user(username)
        if user is None:
            return []
        if user.configs:
            return list(user.configs)
        if user.subscription_url:
            return await self._links_from_subscription(user.subscription_url)
        return []

    async def _links_from_subscription(self, url: str) -> list[str]:
        """Fetch a subscription and decode it into URIs.

        The request carries **no Authorization header**. A subscription URL is
        public by its token and is often served from a different host than the
        panel's API; sending the panel's admin token there would hand full
        control of the panel to whoever runs that host.
        """
        client = await self._http()
        target = (
            url
            if url.startswith(("http://", "https://"))
            else f"{self.credentials.base_url.rstrip('/')}/{url.lstrip('/')}"
        )
        try:
            response = await client.get(
                target,
                # A v2ray client's user agent makes Marzban-family panels
                # return the base64 link list rather than a Clash or sing-box
                # document.
                headers={"User-Agent": "v2rayNG/1.8.5", "Accept": "*/*"},
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise PanelError(
                "Could not fetch the user's subscription",
                code="PANEL_SUBSCRIPTION_FAILED",
            ) from exc
        if response.status_code >= 400:
            raise PanelError(
                f"Subscription fetch returned HTTP {response.status_code}",
                code="PANEL_SUBSCRIPTION_FAILED",
            )
        return decode_subscription(response.text)

    async def renew_user(
        self,
        username: str,
        *,
        traffic_limit_bytes: int,
        expire_at: datetime | None,
        reset_usage: bool = True,
    ) -> PanelUser:
        if reset_usage:
            response = await self._request(
                "POST", ENDPOINTS["user_reset"].format(username=username)
            )
            if response.status_code >= 400 and response.status_code != 404:
                raise PanelError(
                    f"Panel refused the usage reset (HTTP {response.status_code})",
                    code="PANEL_RENEW_FAILED",
                )
        return await self.update_user(
            username,
            **{
                FIELDS["data_limit"]: traffic_limit_bytes,
                FIELDS["expire"]: int(expire_at.timestamp()) if expire_at else 0,
                FIELDS["status"]: "active",
            },
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._token = None
