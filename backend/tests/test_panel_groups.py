"""Choosing which panel groups new users are placed in.

The setting is validated against the panel itself: an id that does not exist,
is disabled, or grants no inbound would let every purchase "succeed" while
delivering no config.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.security import create_token, hash_password
from app.models.enums import AdminRole, PanelType
from app.models.panel import Panel
from app.models.user import User
from tests.test_panel_adapter_http import FakePanel, make_adapter


@pytest.fixture
def fake_panel(monkeypatch):
    panel = FakePanel(group_based=True)
    monkeypatch.setattr(
        "app.panels.manager.PanelManager.adapter_for",
        lambda self, _panel: make_adapter(panel),
    )
    return panel


@pytest.fixture
async def client(session):
    from app.api.deps import get_session
    from app.main import app

    async def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c
    app.dependency_overrides.clear()


async def _setup(session, role=AdminRole.OWNER):
    user = User(
        username="op",
        email="op@example.com",
        password_hash=hash_password("Str0ng-Passw0rd-for-tests-only"),
        admin_role=role,
    )
    panel = Panel(
        name="main", panel_type=PanelType.PASARGUARD, base_url="https://p.example"
    )
    session.add_all([user, panel])
    await session.flush()
    token, _, _ = create_token(user.id, "access")
    return {"Authorization": f"Bearer {token}"}, panel


def _url(panel_id: str) -> str:
    return f"/api/v1/admin/panels/{panel_id}/groups"


async def test_lists_the_panels_groups_live(client, session, fake_panel):
    auth, panel = await _setup(session)

    r = await client.get(_url(panel.id), headers=auth)

    assert r.status_code == 200, r.text
    names = {g["name"] for g in r.json()["data"]}
    assert names == {"free", "TEST", "old", "empty"}


async def test_sets_a_valid_group_and_marks_it_default(client, session, fake_panel):
    auth, panel = await _setup(session)

    r = await client.put(_url(panel.id), json={"group_ids": [1]}, headers=auth)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["default_group_ids"] == [1]
    listed = (await client.get(_url(panel.id), headers=auth)).json()["data"]
    assert [g["name"] for g in listed if g["is_default"]] == ["free"]


@pytest.mark.parametrize(
    ("group_id", "reason"),
    [(99, "does not exist"), (3, "is disabled"), (4, "grants no inbound")],
)
async def test_refuses_a_group_that_would_deliver_nothing(
    client, session, fake_panel, group_id, reason
):
    auth, panel = await _setup(session)

    r = await client.put(_url(panel.id), json={"group_ids": [group_id]}, headers=auth)

    assert r.status_code == 422
    assert reason in r.json()["error"]["message"]
    await session.refresh(panel)
    assert panel.group_id_list == []


async def test_support_cannot_change_groups(client, session, fake_panel):
    auth, panel = await _setup(session, role=AdminRole.SUPPORT)

    r = await client.put(_url(panel.id), json={"group_ids": [1]}, headers=auth)

    assert r.status_code == 403
