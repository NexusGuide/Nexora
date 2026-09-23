"""Recovering a customer who paid and received nothing.

Provisioning dead-letters after its last retry, and if it failed before the
panel account existed, the subscription row was rolled back with it. The order
is then PAID with no subscription, and before this endpoint there was no way
back short of editing the database.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.core.security import create_token, hash_password
from app.models.billing import Order, Plan
from app.models.enums import AdminRole, OrderStatus
from app.models.user import User
from app.workers.jobs import CREATE_PANEL_USER
from app.workers.queue import InMemoryQueue


def _url(order_id: str) -> str:
    return f"/api/v1/admin/orders/{order_id}/reprovision"


@pytest.fixture
def queue(monkeypatch):
    """Capture what the endpoint enqueues instead of needing Redis."""
    q = InMemoryQueue()
    monkeypatch.setattr("app.api.v1.admin.build_queue", lambda _url: q)
    # The endpoint closes the queue in a finally block; keep this one readable.
    monkeypatch.setattr(q, "close", _noop)
    return q


async def _noop():
    return None


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


async def _user(session, name, role=None):
    u = User(
        username=name,
        email=f"{name}@example.com",
        password_hash=hash_password("Str0ng-Passw0rd-for-tests-only"),
        admin_role=role,
    )
    session.add(u)
    await session.flush()
    return u


def _auth(user) -> dict[str, str]:
    token, _, _ = create_token(user.id, "access")
    return {"Authorization": f"Bearer {token}"}


async def _order(session, owner, status):
    plan = Plan(
        name="p",
        duration_days=30,
        traffic_limit_bytes=0,
        device_limit=1,
        price=Decimal("10.00"),
    )
    session.add(plan)
    await session.flush()
    order = Order(
        user_id=owner.id,
        plan_id=plan.id,
        amount=Decimal("10.00"),
        status=status,
        idempotency_key=f"k-{owner.id}-{status}",
        plan_snapshot=json.dumps({"duration_days": 30}),
    )
    session.add(order)
    await session.flush()
    return order


async def test_a_stranded_paid_order_is_queued_for_provisioning(client, session, queue):
    admin = await _user(session, "admin", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    order = await _order(session, buyer, OrderStatus.PAID)
    assert order.subscription_id is None  # the stranded state

    r = await client.post(_url(order.id), headers=_auth(admin))

    assert r.status_code == 200, r.text
    jobs = await queue.reserve(block_ms=0)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.name == CREATE_PANEL_USER
    assert job.payload == {"order_id": order.id}


async def test_an_unpaid_order_is_refused(client, session, queue):
    admin = await _user(session, "admin", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    order = await _order(session, buyer, OrderStatus.PENDING)

    r = await client.post(_url(order.id), headers=_auth(admin))

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ORDER_NOT_PAID"
    assert await queue.reserve(block_ms=0) == []


async def test_a_customer_cannot_reprovision(client, session, queue):
    buyer = await _user(session, "buyer")
    order = await _order(session, buyer, OrderStatus.PAID)

    r = await client.post(_url(order.id), headers=_auth(buyer))

    assert r.status_code == 403
    assert await queue.reserve(block_ms=0) == []


async def test_an_unknown_order_is_404(client, session, queue):
    admin = await _user(session, "admin", AdminRole.OWNER)

    r = await client.post("/api/v1/admin/orders/nope/reprovision", headers=_auth(admin))

    assert r.status_code == 404
