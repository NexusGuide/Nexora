"""The admin API behind the web panel, and the panel's own delivery.

Each endpoint is tested three ways: it does its job for a permitted role, it
refuses a role that is not permitted, and — for the account-changing route —
it refuses the self-harm and takeover cases it exists to prevent.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.admin_roles import CAPABILITIES, capabilities_of
from app.api.admin_web import ADMIN_CSP
from app.models.billing import Order, Plan, Subscription
from app.models.enums import (
    AdminRole,
    DeviceStatus,
    OrderStatus,
    PanelStatus,
    PanelType,
    SubscriptionStatus,
    UserStatus,
)
from app.models.panel import AuditLog, Panel
from app.models.user import RefreshToken, UserDevice
from app.workers.queue import InMemoryQueue
from tests.test_order_reprovision import _auth, _order, _user, client  # noqa: F401

A = "/api/v1/admin"
PASSWORD = "Str0ng-Passw0rd-for-tests-only"


# ------------------------------------------------------------------ helpers
async def _plan(session, name="Gold", price="100.00"):
    plan = Plan(
        name=name,
        duration_days=30,
        traffic_limit_bytes=10 * 1024**3,
        device_limit=2,
        price=Decimal(price),
    )
    session.add(plan)
    await session.flush()
    return plan


async def _subscription(session, user, plan, status=SubscriptionStatus.ACTIVE, days=20):
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        status=status,
        panel_username=f"nx_{user.username}",
        start_at=datetime.now(UTC),
        expire_at=datetime.now(UTC) + timedelta(days=days),
        traffic_limit_bytes=plan.traffic_limit_bytes,
        traffic_used_bytes=1024**3,
        device_limit=2,
        # A working credential for the customer's service; must never appear
        # in an admin response.
        subscription_url="https://sub.example.invalid/secret-link-for-tests-only",
    )
    session.add(sub)
    await session.flush()
    return sub


async def _refresh_token(session, user, device_id=None):
    token = RefreshToken(
        user_id=user.id,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        jti=uuid.uuid4().hex,
        family_id=uuid.uuid4().hex,
        device_id=device_id,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add(token)
    await session.flush()
    return token


async def _device(session, user, device_id="dev-1", name="Pixel"):
    device = UserDevice(
        user_id=user.id,
        device_id=device_id,
        device_name=name,
        status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    await session.flush()
    return device


async def _audit_actions(session, entity_id):
    rows = await session.scalars(select(AuditLog).where(AuditLog.entity_id == entity_id))
    return [r.action for r in rows]


def _no_secrets(body: str):
    assert "password_hash" not in body
    assert "argon2" not in body
    assert "secret-link-for-tests-only" not in body
    assert "subscription_url" not in body


# ------------------------------------------------------------------- roles
def test_capabilities_owner_has_everything_and_customers_nothing():
    assert capabilities_of(AdminRole.OWNER) == sorted(CAPABILITIES)
    assert capabilities_of(None) == []
    # roles.write is OWNER only: no other role may hold it.
    for role in AdminRole:
        if role is not AdminRole.OWNER:
            assert "roles.write" not in capabilities_of(role)


# ---------------------------------------------------------------------- me
async def test_me_describes_the_admin_and_their_capabilities(client, session):  # noqa: F811
    support = await _user(session, "helpdesk", AdminRole.SUPPORT)

    r = await client.get(f"{A}/me", headers=_auth(support))

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["username"] == "helpdesk"
    assert data["role"] == "SUPPORT"
    assert "users.read" in data["capabilities"]
    assert "orders" not in data["capabilities"]
    assert "audit.read" not in data["capabilities"]


async def test_me_refuses_a_customer(client, session):  # noqa: F811
    buyer = await _user(session, "buyer")

    r = await client.get(f"{A}/me", headers=_auth(buyer))

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PERMISSION_DENIED"


# ------------------------------------------------------------------- stats
async def test_stats_are_real_counts(client, session):  # noqa: F811
    finance = await _user(session, "money", AdminRole.FINANCE)
    alice = await _user(session, "alice")
    bob = await _user(session, "bob")
    bob.status = UserStatus.SUSPENDED
    plan = await _plan(session)
    await _subscription(session, alice, plan, days=3)  # expires within 7 days
    await _subscription(session, bob, plan, status=SubscriptionStatus.EXPIRED)
    await _order(session, alice, OrderStatus.PENDING)
    paid = await _order(session, bob, OrderStatus.PAID)
    paid.completed_at = datetime.now(UTC) - timedelta(days=2)
    old = Order(
        user_id=alice.id,
        plan_id=plan.id,
        amount=Decimal("999.00"),
        status=OrderStatus.PAID,
        idempotency_key="old-paid-order",
        plan_snapshot="{}",
        completed_at=datetime.now(UTC) - timedelta(days=45),
    )
    session.add(old)
    session.add(
        Panel(
            name="p1",
            panel_type=PanelType.PASARGUARD,
            base_url="https://a",
            last_checked_at=datetime.now(UTC),
        )
    )
    # Never tested: its status defaults to ACTIVE, but that proves nothing.
    session.add(Panel(name="p0", panel_type=PanelType.PASARGUARD, base_url="https://c"))
    session.add(
        Panel(
            name="p2",
            panel_type=PanelType.PASARGUARD,
            base_url="https://b",
            status=PanelStatus.UNREACHABLE,
            last_checked_at=datetime.now(UTC),
        )
    )
    await session.flush()

    r = await client.get(f"{A}/stats", headers=_auth(finance))

    assert r.status_code == 200, r.text
    s = r.json()["data"]
    assert s["users"]["total"] == 3
    assert s["users"]["by_status"]["SUSPENDED"] == 1
    assert s["subscriptions"]["active"] == 1
    assert s["subscriptions"]["expiring_7d"] == 1
    assert s["orders"]["pending"] == 1
    # Both PAID orders have no subscription attached.
    assert s["orders"]["paid_unprovisioned"] == 2
    # Only the order paid in the last 30 days counts; the 45-day-old one
    # does not.
    assert s["revenue_30d"] == [{"currency": "IRT", "orders": 1, "amount": "10.00"}]
    assert s["panels"] == {
        "total": 3,
        "reachable": 1,
        "untested": 1,
        "by_status": {"ACTIVE": 2, "UNREACHABLE": 1},
    }


async def test_stats_hide_revenue_from_roles_without_it(client, session):  # noqa: F811
    support = await _user(session, "helpdesk", AdminRole.SUPPORT)

    r = await client.get(f"{A}/stats", headers=_auth(support))

    assert r.status_code == 200
    assert r.json()["data"]["revenue_30d"] is None


async def test_stats_refuse_a_customer(client, session):  # noqa: F811
    buyer = await _user(session, "buyer")
    r = await client.get(f"{A}/stats", headers=_auth(buyer))
    assert r.status_code == 403


# ------------------------------------------------------------------- users
async def test_users_can_be_searched_by_email(client, session):  # noqa: F811
    support = await _user(session, "helpdesk", AdminRole.SUPPORT)
    alice = await _user(session, "alice")
    await _user(session, "bob")
    plan = await _plan(session)
    await _subscription(session, alice, plan)

    r = await client.get(
        f"{A}/users", params={"q": "ALICE@example"}, headers=_auth(support)
    )

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 1
    [row] = data["items"]
    assert row["username"] == "alice"
    assert row["active_subscriptions"] == 1
    assert row["latest_expire_at"] is not None
    _no_secrets(r.text)


async def test_user_search_treats_wildcards_literally(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    await _user(session, "a_b")
    await _user(session, "axb")

    r = await client.get(f"{A}/users", params={"q": "a_b"}, headers=_auth(manager))

    assert [u["username"] for u in r.json()["data"]["items"]] == ["a_b"]


async def test_user_list_is_paged(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    for i in range(4):
        await _user(session, f"user{i}")

    r = await client.get(
        f"{A}/users", params={"limit": 2, "offset": 2}, headers=_auth(manager)
    )

    data = r.json()["data"]
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert (data["limit"], data["offset"]) == (2, 2)


async def test_finance_cannot_read_customer_accounts(client, session):  # noqa: F811
    finance = await _user(session, "money", AdminRole.FINANCE)
    r = await client.get(f"{A}/users", headers=_auth(finance))
    assert r.status_code == 403


async def test_user_detail_has_service_devices_and_orders(client, session):  # noqa: F811
    support = await _user(session, "helpdesk", AdminRole.SUPPORT)
    alice = await _user(session, "alice")
    plan = await _plan(session)
    await _subscription(session, alice, plan)
    await _device(session, alice)
    await _order(session, alice, OrderStatus.PAID)

    r = await client.get(f"{A}/users/{alice.id}", headers=_auth(support))

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["username"] == "alice"
    assert [s["plan_name"] for s in data["subscriptions"]] == ["Gold"]
    assert data["subscriptions"][0]["traffic_used_bytes"] == 1024**3
    assert [d["device_name"] for d in data["devices"]] == ["Pixel"]
    assert "device_id" not in data["devices"][0]
    assert [o["status"] for o in data["orders"]] == ["PAID"]
    _no_secrets(r.text)


async def test_unknown_user_is_404(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    r = await client.get(f"{A}/users/nope", headers=_auth(manager))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "USER_NOT_FOUND"


# ------------------------------------------------------------ user updates
async def test_suspending_revokes_sessions_and_is_audited(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    alice = await _user(session, "alice")
    t1 = await _refresh_token(session, alice)
    t2 = await _refresh_token(session, alice, device_id="dev-1")

    r = await client.patch(
        f"{A}/users/{alice.id}", json={"status": "SUSPENDED"}, headers=_auth(manager)
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "SUSPENDED"
    assert r.json()["data"]["sessions_revoked"] == 2
    for token in (t1, t2):
        await session.refresh(token)
        assert token.revoked_at is not None
        assert token.revoked_reason == "account_suspended"
    assert await _audit_actions(session, alice.id) == ["user.update"]

    # Access tokens already issued stop working at once.
    r = await client.get("/api/v1/auth/me", headers=_auth(alice))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ACCOUNT_INACTIVE"


async def test_reactivating_does_not_revoke_anything(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    alice = await _user(session, "alice")
    alice.status = UserStatus.BANNED
    await session.flush()

    r = await client.patch(
        f"{A}/users/{alice.id}", json={"status": "ACTIVE"}, headers=_auth(manager)
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "ACTIVE"
    assert r.json()["data"]["sessions_revoked"] == 0


async def test_support_cannot_suspend(client, session):  # noqa: F811
    support = await _user(session, "helpdesk", AdminRole.SUPPORT)
    alice = await _user(session, "alice")

    r = await client.patch(
        f"{A}/users/{alice.id}", json={"status": "BANNED"}, headers=_auth(support)
    )

    assert r.status_code == 403
    await session.refresh(alice)
    assert alice.status is UserStatus.ACTIVE


async def test_nobody_can_suspend_themselves(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)

    r = await client.patch(
        f"{A}/users/{manager.id}", json={"status": "SUSPENDED"}, headers=_auth(manager)
    )

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CANNOT_MODIFY_SELF"


async def test_an_owner_cannot_demote_themselves(client, session):  # noqa: F811
    owner = await _user(session, "root", AdminRole.OWNER)

    r = await client.patch(
        f"{A}/users/{owner.id}", json={"admin_role": None}, headers=_auth(owner)
    )

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CANNOT_MODIFY_SELF"
    await session.refresh(owner)
    assert owner.admin_role is AdminRole.OWNER


async def test_a_manager_cannot_change_roles(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    alice = await _user(session, "alice")

    r = await client.patch(
        f"{A}/users/{alice.id}", json={"admin_role": "MANAGER"}, headers=_auth(manager)
    )

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ROLE_CHANGE_FORBIDDEN"
    await session.refresh(alice)
    assert alice.admin_role is None


async def test_a_manager_cannot_touch_an_owner(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    owner = await _user(session, "root", AdminRole.OWNER)

    r = await client.patch(
        f"{A}/users/{owner.id}", json={"status": "BANNED"}, headers=_auth(manager)
    )

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "TARGET_IS_OWNER"


async def test_an_owner_can_grant_and_remove_roles(client, session):  # noqa: F811
    owner = await _user(session, "root", AdminRole.OWNER)
    alice = await _user(session, "alice")

    r = await client.patch(
        f"{A}/users/{alice.id}", json={"admin_role": "SUPPORT"}, headers=_auth(owner)
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["admin_role"] == "SUPPORT"

    r = await client.patch(
        f"{A}/users/{alice.id}", json={"admin_role": None}, headers=_auth(owner)
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["admin_role"] is None
    assert await _audit_actions(session, alice.id) == ["user.update", "user.update"]


async def test_an_owner_can_demote_another_owner(client, session):  # noqa: F811
    owner = await _user(session, "root", AdminRole.OWNER)
    other = await _user(session, "root2", AdminRole.OWNER)

    r = await client.patch(
        f"{A}/users/{other.id}", json={"admin_role": "MANAGER"}, headers=_auth(owner)
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["admin_role"] == "MANAGER"


async def test_the_last_active_owner_cannot_be_removed(client, session, monkeypatch):  # noqa: F811
    """Through the API this is already unreachable — nobody may change their
    own account and only an owner may touch an owner — so the guard is
    defence in depth. Simulate the race it exists for: the other owner was
    disabled between the request arriving and the count being taken."""
    owner = await _user(session, "root", AdminRole.OWNER)
    other = await _user(session, "root2", AdminRole.OWNER)

    async def _only_one(_session):
        return 1

    monkeypatch.setattr("app.api.v1.admin_panel._active_owner_count", _only_one)

    r = await client.patch(
        f"{A}/users/{other.id}", json={"status": "SUSPENDED"}, headers=_auth(owner)
    )

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "LAST_OWNER"
    await session.refresh(other)
    assert other.status is UserStatus.ACTIVE


async def test_pending_is_not_an_assignable_status(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    alice = await _user(session, "alice")

    r = await client.patch(
        f"{A}/users/{alice.id}", json={"status": "PENDING"}, headers=_auth(manager)
    )

    assert r.status_code == 422


# ----------------------------------------------------------------- devices
async def test_support_can_revoke_a_device_and_its_sessions(client, session):  # noqa: F811
    support = await _user(session, "helpdesk", AdminRole.SUPPORT)
    alice = await _user(session, "alice")
    device = await _device(session, alice, device_id="lost-phone")
    token = await _refresh_token(session, alice, device_id="lost-phone")
    other = await _refresh_token(session, alice, device_id="tablet")

    r = await client.delete(
        f"{A}/users/{alice.id}/devices/{device.id}", headers=_auth(support)
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "REVOKED"
    await session.refresh(token)
    await session.refresh(other)
    assert token.revoked_reason == "device_revoked"
    assert other.revoked_at is None
    assert await _audit_actions(session, device.id) == ["device.revoke"]


async def test_a_device_of_another_user_is_404(client, session):  # noqa: F811
    support = await _user(session, "helpdesk", AdminRole.SUPPORT)
    alice = await _user(session, "alice")
    bob = await _user(session, "bob")
    bobs = await _device(session, bob)

    r = await client.delete(
        f"{A}/users/{alice.id}/devices/{bobs.id}", headers=_auth(support)
    )

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "DEVICE_NOT_FOUND"


async def test_finance_cannot_revoke_devices(client, session):  # noqa: F811
    finance = await _user(session, "money", AdminRole.FINANCE)
    alice = await _user(session, "alice")
    device = await _device(session, alice)

    r = await client.delete(
        f"{A}/users/{alice.id}/devices/{device.id}", headers=_auth(finance)
    )

    assert r.status_code == 403
    await session.refresh(device)
    assert device.status is DeviceStatus.ACTIVE


# ----------------------------------------------------------- subscriptions
async def test_subscriptions_can_be_filtered_by_status(client, session):  # noqa: F811
    dev = await _user(session, "dev", AdminRole.DEVELOPER)
    alice = await _user(session, "alice")
    plan = await _plan(session)
    active = await _subscription(session, alice, plan)
    await _subscription(session, alice, plan, status=SubscriptionStatus.EXPIRED)

    r = await client.get(
        f"{A}/subscriptions", params={"status": "ACTIVE"}, headers=_auth(dev)
    )

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 1
    [row] = data["items"]
    assert row["id"] == active.id
    assert row["username"] == "alice"
    assert row["plan_name"] == "Gold"
    assert row["traffic_limit_bytes"] == 10 * 1024**3
    _no_secrets(r.text)


async def test_finance_cannot_list_subscriptions(client, session):  # noqa: F811
    finance = await _user(session, "money", AdminRole.FINANCE)
    r = await client.get(f"{A}/subscriptions", headers=_auth(finance))
    assert r.status_code == 403


async def test_refreshing_a_subscriptions_configs_is_audited(
    client,  # noqa: F811
    session,
    monkeypatch,
):
    queue = InMemoryQueue()

    async def _noop():
        return None

    monkeypatch.setattr("app.api.v1.admin.build_queue", lambda _url: queue)
    monkeypatch.setattr(queue, "close", _noop)
    dev = await _user(session, "dev", AdminRole.DEVELOPER)
    alice = await _user(session, "alice")
    sub = await _subscription(session, alice, await _plan(session))

    r = await client.post(f"{A}/subscriptions/{sub.id}/reprovision", headers=_auth(dev))

    assert r.status_code == 200, r.text
    assert await _audit_actions(session, sub.id) == ["subscription.refresh_configs"]


# ------------------------------------------------------------------- audit
async def test_audit_log_lists_actions_newest_first(client, session):  # noqa: F811
    manager = await _user(session, "boss", AdminRole.MANAGER)
    alice = await _user(session, "alice")
    await client.patch(
        f"{A}/users/{alice.id}", json={"status": "SUSPENDED"}, headers=_auth(manager)
    )

    r = await client.get(f"{A}/audit", headers=_auth(manager))

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 1
    [entry] = data["items"]
    assert entry["actor_username"] == "boss"
    assert entry["action"] == "user.update"
    assert entry["entity"] == "user"
    assert entry["entity_id"] == alice.id
    assert entry["metadata"]["status"] == {"from": "ACTIVE", "to": "SUSPENDED"}


async def test_support_cannot_read_the_audit_log(client, session):  # noqa: F811
    support = await _user(session, "helpdesk", AdminRole.SUPPORT)
    r = await client.get(f"{A}/audit", headers=_auth(support))
    assert r.status_code == 403


# ------------------------------------------------------------------- plans
async def test_admin_plan_list_carries_the_sort_order(client, session):  # noqa: F811
    finance = await _user(session, "money", AdminRole.FINANCE)
    plan = await _plan(session)
    plan.sort_order = 7
    await session.flush()

    r = await client.get(f"{A}/plans", headers=_auth(finance))

    assert r.status_code == 200, r.text
    assert r.json()["data"][0]["sort_order"] == 7


# ----------------------------------------------------- sign-in round trip
async def test_panel_sign_in_round_trip_registers_no_device(client, session):  # noqa: F811
    """What the web panel does on sign-in: login without a device_id, then
    /admin/me, then the dashboard."""
    await _user(session, "root", AdminRole.OWNER)
    await session.commit()

    r = await client.post(
        "/api/v1/auth/login", json={"identifier": "root", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    access = r.json()["data"]["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    me = await client.get(f"{A}/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["data"]["role"] == "OWNER"

    stats = await client.get(f"{A}/stats", headers=headers)
    assert stats.status_code == 200
    assert stats.json()["data"]["users"]["total"] == 1

    assert (await session.scalars(select(UserDevice))).all() == []


# -------------------------------------------------------------- web panel
@pytest.mark.parametrize(
    ("path", "content_type", "cache"),
    [
        ("/admin", "text/html", "no-store"),
        ("/admin/", "text/html", "no-store"),
        ("/admin/app.js", "text/javascript", "no-cache"),
        ("/admin/app.css", "text/css", "no-cache"),
    ],
)
async def test_panel_files_are_served_with_a_strict_policy(
    client,  # noqa: F811
    path,
    content_type,
    cache,
):
    r = await client.get(path)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith(content_type)
    assert r.headers["content-security-policy"] == ADMIN_CSP
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == cache


def test_panel_policy_allows_nothing_inline_or_foreign():
    directives = dict(d.strip().split(" ", 1) for d in ADMIN_CSP.split(";"))
    assert directives["default-src"] == "'self'"
    assert directives["script-src"] == "'self'"
    assert directives["style-src"] == "'self'"
    assert directives["connect-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["base-uri"] == "'none'"
    assert "unsafe" not in ADMIN_CSP


async def test_the_api_keeps_its_own_locked_down_policy(client):  # noqa: F811
    r = await client.get("/")
    assert r.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )


async def test_only_the_three_panel_files_are_reachable(client):  # noqa: F811
    for path in ("/admin/index.html.bak", "/admin/.env", "/admin/admin_web/app.js"):
        r = await client.get(path)
        assert r.status_code == 404, path


WEB = Path(__file__).resolve().parent.parent / "app" / "admin_web"


def test_panel_source_never_parses_html_from_data():
    """A regression guard for the rule the panel's XSS safety rests on."""
    js = (WEB / "app.js").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in js.splitlines() if not line.lstrip().startswith("//")
    )
    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert forbidden not in code, forbidden


def test_panel_page_has_no_inline_script_style_or_handlers():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert re.search(r"<script(?![^>]*\bsrc=)", html) is None
    assert "<style" not in html
    assert re.search(r"\sstyle=", html) is None
    assert re.search(r"\son[a-z]+=", html) is None
    # Nothing is loaded from another origin.
    assert re.search(r"(src|href)=\"(https?:)?//", html) is None
