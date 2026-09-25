"""Finding the orders that wait for a manual payment confirmation."""

from __future__ import annotations

from app.models.enums import AdminRole, OrderStatus
from tests.test_order_reprovision import _auth, _order, _user, client  # noqa: F401

URL = "/api/v1/admin/orders"


async def test_pending_orders_can_be_listed_with_who_and_what(client, session):  # noqa: F811
    admin = await _user(session, "admin", AdminRole.FINANCE)
    buyer = await _user(session, "buyer")
    pending = await _order(session, buyer, OrderStatus.PENDING)
    await _order(session, buyer, OrderStatus.PAID)

    r = await client.get(URL, params={"order_status": "PENDING"}, headers=_auth(admin))

    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert [row["id"] for row in rows] == [pending.id]
    assert rows[0]["username"] == "buyer"
    assert rows[0]["plan_name"] == "p"
    assert rows[0]["amount"] == "10.00"
    # Nothing beyond what checking a payment needs.
    assert "email" not in rows[0]


async def test_without_a_filter_every_order_is_listed(client, session):  # noqa: F811
    admin = await _user(session, "admin", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _order(session, buyer, OrderStatus.PENDING)
    await _order(session, buyer, OrderStatus.PAID)

    r = await client.get(URL, headers=_auth(admin))

    assert r.status_code == 200
    assert {row["status"] for row in r.json()["data"]} == {"PENDING", "PAID"}


async def test_a_customer_cannot_list_orders(client, session):  # noqa: F811
    buyer = await _user(session, "buyer")
    await _order(session, buyer, OrderStatus.PENDING)

    r = await client.get(URL, headers=_auth(buyer))

    assert r.status_code == 403


async def test_support_cannot_list_orders(client, session):  # noqa: F811
    support = await _user(session, "support", AdminRole.SUPPORT)

    r = await client.get(URL, headers=_auth(support))

    assert r.status_code == 403


async def test_an_admin_can_cancel_a_waiting_order(client, session):  # noqa: F811
    admin = await _user(session, "admin", AdminRole.MANAGER)
    buyer = await _user(session, "buyer")
    order = await _order(session, buyer, OrderStatus.PENDING)

    r = await client.post(f"{URL}/{order.id}/cancel", headers=_auth(admin))

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "CANCELLED"


async def test_a_paid_order_cannot_be_cancelled(client, session):  # noqa: F811
    admin = await _user(session, "admin", AdminRole.MANAGER)
    buyer = await _user(session, "buyer")
    order = await _order(session, buyer, OrderStatus.PAID)

    r = await client.post(f"{URL}/{order.id}/cancel", headers=_auth(admin))

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ORDER_NOT_CANCELLABLE"


async def test_a_customer_cannot_cancel_through_the_admin_route(client, session):  # noqa: F811
    buyer = await _user(session, "buyer")
    order = await _order(session, buyer, OrderStatus.PENDING)

    r = await client.post(f"{URL}/{order.id}/cancel", headers=_auth(buyer))

    assert r.status_code == 403
