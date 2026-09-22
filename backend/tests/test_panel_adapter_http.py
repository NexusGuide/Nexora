"""PasarGuard adapter against a mocked HTTP panel.

The panel is simulated with ``httpx.MockTransport``. The suite must never
reach a real panel: a test that needs live infrastructure gets skipped, and a
skipped test protects nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.exceptions import PanelError
from app.panels.base import PanelCredentials
from app.panels.pasarguard import PasarGuardAdapter

GB = 1024**3


class FakePanel:
    """A minimal PasarGuard-shaped API, with switches for failure modes."""

    def __init__(self, *, auth_ok: bool = True, expire_token_once: bool = False):
        self.users: dict[str, dict] = {}
        self.auth_ok = auth_ok
        self.expire_token_once = expire_token_once
        self.token_used = False
        self.create_calls = 0
        self.auth_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path == "/api/admin/token":
            self.auth_calls += 1
            if not self.auth_ok:
                return httpx.Response(401, json={"detail": "bad credentials"})
            return httpx.Response(200, json={"access_token": "panel-token-abc"})

        if self.expire_token_once and not self.token_used:
            self.token_used = True
            return httpx.Response(401, json={"detail": "token expired"})

        if path == "/api/users":
            return httpx.Response(200, json={"users": list(self.users.values())})

        if path == "/api/user" and request.method == "POST":
            self.create_calls += 1
            body = json.loads(request.content)
            username = body["username"]
            self.users[username] = {
                "username": username,
                "status": "active",
                "data_limit": body.get("data_limit", 0),
                "used_traffic": 0,
                "expire": body.get("expire", 0),
                "subscription_url": f"https://panel.example/sub/{username}",
                "links": [
                    f"vless://uuid-1@de1.example.com:443?type=ws#{username}-DE",
                    f"vless://uuid-2@nl1.example.com:443?type=grpc#{username}-NL",
                ],
            }
            return httpx.Response(200, json=self.users[username])

        if path.startswith("/api/user/"):
            username = path.split("/api/user/")[1].split("/")[0]
            if path.endswith("/reset"):
                if username in self.users:
                    self.users[username]["used_traffic"] = 0
                return httpx.Response(200, json={})

            if request.method == "GET":
                if username not in self.users:
                    return httpx.Response(404, json={"detail": "not found"})
                return httpx.Response(200, json=self.users[username])

            if request.method == "PUT":
                if username not in self.users:
                    return httpx.Response(404, json={"detail": "not found"})
                self.users[username].update(json.loads(request.content))
                return httpx.Response(200, json=self.users[username])

            if request.method == "DELETE":
                self.users.pop(username, None)
                return httpx.Response(204)

        return httpx.Response(404, json={"detail": "unhandled"})


def make_adapter(panel: FakePanel) -> PasarGuardAdapter:
    adapter = PasarGuardAdapter(
        PanelCredentials(
            base_url="https://panel.example.com",
            username="admin",
            password="panel-secret",
        )
    )
    adapter._client = httpx.AsyncClient(
        base_url="https://panel.example.com",
        transport=httpx.MockTransport(panel.handler),
    )
    return adapter


# --- connection -------------------------------------------------------------
async def test_connection_succeeds(session):
    panel = FakePanel()
    async with make_adapter(panel) as adapter:
        assert await adapter.test_connection() is True


async def test_bad_credentials_raise_a_clean_error(session):
    """The panel's own message must not reach the client."""
    panel = FakePanel(auth_ok=False)
    async with make_adapter(panel) as adapter:
        with pytest.raises(PanelError) as exc:
            await adapter.test_connection()
    assert exc.value.code == "PANEL_AUTH_FAILED"
    assert "bad credentials" not in exc.value.message


async def test_expired_token_is_refreshed_once(session):
    """A cached token that expires mid-session must not fail the operation."""
    panel = FakePanel(expire_token_once=True)
    async with make_adapter(panel) as adapter:
        assert await adapter.test_connection() is True
    assert panel.auth_calls == 2


# --- user lifecycle ---------------------------------------------------------
async def test_create_user_returns_normalised_fields(session):
    panel = FakePanel()
    expire = datetime.now(UTC) + timedelta(days=30)
    async with make_adapter(panel) as adapter:
        user = await adapter.create_user(
            "nx_test1", traffic_limit_bytes=100 * GB, expire_at=expire
        )

    assert user.username == "nx_test1"
    assert user.traffic_limit_bytes == 100 * GB
    assert user.traffic_used_bytes == 0
    assert user.expire_at is not None
    assert abs((user.expire_at - expire).total_seconds()) < 2
    assert len(user.configs) == 2


async def test_create_user_is_idempotent(session):
    """A retried provisioning job must not create a second panel account."""
    panel = FakePanel()
    expire = datetime.now(UTC) + timedelta(days=30)
    async with make_adapter(panel) as adapter:
        await adapter.create_user("nx_dup", traffic_limit_bytes=10 * GB, expire_at=expire)
        await adapter.create_user("nx_dup", traffic_limit_bytes=10 * GB, expire_at=expire)

    assert panel.create_calls == 1
    assert len(panel.users) == 1


async def test_get_user_returns_none_when_absent(session):
    panel = FakePanel()
    async with make_adapter(panel) as adapter:
        assert await adapter.get_user("nobody") is None


async def test_disable_and_enable(session):
    panel = FakePanel()
    async with make_adapter(panel) as adapter:
        await adapter.create_user("nx_d", traffic_limit_bytes=0, expire_at=None)
        await adapter.disable_user("nx_d")
        assert panel.users["nx_d"]["status"] == "disabled"
        await adapter.enable_user("nx_d")
        assert panel.users["nx_d"]["status"] == "active"


async def test_usage_is_reported_in_bytes(session):
    panel = FakePanel()
    async with make_adapter(panel) as adapter:
        await adapter.create_user("nx_u", traffic_limit_bytes=50 * GB, expire_at=None)
        panel.users["nx_u"]["used_traffic"] = 12 * GB
        usage = await adapter.get_usage("nx_u")

    assert usage.used_bytes == 12 * GB
    assert usage.limit_bytes == 50 * GB


async def test_usage_for_a_missing_user_raises(session):
    panel = FakePanel()
    async with make_adapter(panel) as adapter:
        with pytest.raises(PanelError) as exc:
            await adapter.get_usage("ghost")
    assert exc.value.code == "PANEL_NO_USER"


async def test_renew_resets_usage_and_extends(session):
    panel = FakePanel()
    async with make_adapter(panel) as adapter:
        await adapter.create_user("nx_r", traffic_limit_bytes=10 * GB, expire_at=None)
        panel.users["nx_r"]["used_traffic"] = 9 * GB

        new_expiry = datetime.now(UTC) + timedelta(days=60)
        await adapter.renew_user(
            "nx_r", traffic_limit_bytes=20 * GB, expire_at=new_expiry
        )

    assert panel.users["nx_r"]["used_traffic"] == 0
    assert panel.users["nx_r"]["data_limit"] == 20 * GB
    assert panel.users["nx_r"]["status"] == "active"


async def test_delete_is_tolerant_of_a_missing_user(session):
    """Deleting something already gone is success, not an error."""
    panel = FakePanel()
    async with make_adapter(panel) as adapter:
        assert await adapter.delete_user("never-existed") is True


async def test_unreachable_panel_gives_a_clean_error(session):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter = PasarGuardAdapter(
        PanelCredentials(base_url="https://down.example.com", username="a", password="b")
    )
    adapter._client = httpx.AsyncClient(
        base_url="https://down.example.com", transport=httpx.MockTransport(boom)
    )
    async with adapter:
        with pytest.raises(PanelError) as exc:
            await adapter.test_connection()
    assert exc.value.code == "PANEL_UNREACHABLE"
