"""The wallet: top-ups, review, paying orders, corrections and settings.

Money tests assert the ledger and the balance together: a credit that moved
the balance without a ledger row (or the reverse) is the bug that matters.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.models.billing import Order, Plan
from app.models.enums import AdminRole, OrderStatus
from app.models.user import User
from app.models.wallet import TopUp, WalletTransaction
from app.schemas.wallet import CardSettings, CryptoWallet, PaymentSettings, luhn_ok
from tests.test_order_reprovision import _auth, _user

API = "/api/v1"
A = "/api/v1/admin"


def _luhn_card(prefix: str = "603799") -> str:
    """A syntactically valid (Luhn) test card number. Not a real account."""
    body = prefix + "0" * (15 - len(prefix))
    for check in range(10):
        if luhn_ok(body + str(check)):
            return body + str(check)
    raise AssertionError


CARD = _luhn_card()
# Base58 shape of a Tron address; not a real wallet.
TRC20 = "T" + "A" * 33
TX_HASH = "ab" * 32


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


@pytest.fixture
def jobs(monkeypatch):
    """Capture provisioning jobs instead of needing Redis."""
    queued: list[tuple[str, dict]] = []

    async def _enqueue(name, payload):
        queued.append((name, payload))
        return "job-1"

    monkeypatch.setattr("app.api.v1.wallet.enqueue", _enqueue)
    monkeypatch.setattr("app.api.v1.admin_wallet.enqueue", _enqueue)
    return queued


async def _settings(client, owner, **overrides):
    body = {
        "min_topup": "10000",
        "max_topup": "5000000",
        "card": {
            "enabled": True,
            "number": CARD,
            "holder": "Test Holder",
            "bank": "Test",
        },
        "crypto": {
            "enabled": True,
            "wallets": [
                {"network": "TRC20", "asset": "USDT", "address": TRC20, "rate": "60000"}
            ],
        },
    }
    body.update(overrides)
    r = await client.put(f"{A}/payment-settings", json=body, headers=_auth(owner))
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _pending_order(session, user, price="50000"):
    plan = Plan(
        name="Gold",
        duration_days=30,
        traffic_limit_bytes=0,
        device_limit=1,
        price=Decimal(price),
    )
    session.add(plan)
    await session.flush()
    order = Order(
        user_id=user.id,
        plan_id=plan.id,
        amount=Decimal(price),
        currency="IRT",
        status=OrderStatus.PENDING,
        idempotency_key=f"k-{user.id}-{price}",
        plan_snapshot=json.dumps({"duration_days": 30}),
    )
    session.add(order)
    await session.flush()
    return order


async def _topup(client, user, amount="100000", reference="123456789", **extra):
    body = {"method": "CARD", "amount": amount, "reference": reference, **extra}
    return await client.post(f"{API}/wallet/topups", json=body, headers=_auth(user))


async def _ledger(session, user) -> list[WalletTransaction]:
    return list(
        await session.scalars(
            select(WalletTransaction).where(WalletTransaction.user_id == user.id)
        )
    )


async def _balance(session, user) -> Decimal:
    await session.refresh(user)
    return Decimal(user.wallet_balance)


# ----------------------------------------------------------------- settings
def test_card_number_is_checked():
    assert CardSettings(number=CARD).number == CARD
    assert CardSettings(number="۶۰۳۷" + CARD[4:]).number == CARD  # Persian digits
    with pytest.raises(ValueError):
        CardSettings(number=CARD[:-1] + str((int(CARD[-1]) + 1) % 10))
    with pytest.raises(ValueError):
        CardSettings(enabled=True, number="", holder="x")


def test_crypto_address_must_match_its_network():
    CryptoWallet(network="trc20", address=TRC20, rate=Decimal("60000"))
    with pytest.raises(ValueError):
        CryptoWallet(network="TRC20", address="0x" + "a" * 40, rate=Decimal("1"))
    with pytest.raises(ValueError):
        CryptoWallet(network="DOGE", address=TRC20, rate=Decimal("1"))


def test_min_above_max_is_refused():
    with pytest.raises(ValueError):
        PaymentSettings(min_topup=Decimal("10"), max_topup=Decimal("5"))


async def test_only_the_owner_edits_where_money_goes(client, session):
    finance = await _user(session, "fin", AdminRole.FINANCE)
    manager = await _user(session, "mgr", AdminRole.MANAGER)
    for admin in (finance, manager):
        r = await client.put(f"{A}/payment-settings", json={}, headers=_auth(admin))
        assert r.status_code == 403


async def test_customers_see_only_enabled_methods(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner, crypto={"enabled": False, "wallets": []})

    r = await client.get(f"{API}/payment-methods", headers=_auth(buyer))
    data = r.json()["data"]
    assert data["card"]["number"] == CARD
    assert data["crypto"] == []

    assert (await client.get(f"{API}/payment-methods")).status_code == 401


# ------------------------------------------------------------------ top-ups
async def test_a_topup_waits_for_review_and_credits_once(client, session, jobs):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)

    r = await _topup(client, buyer)
    assert r.status_code == 201, r.text
    topup_id = r.json()["data"]["id"]
    assert await _balance(session, buyer) == 0  # nothing until approved

    for _ in range(2):  # a double click must not credit twice
        r = await client.post(f"{A}/topups/{topup_id}/approve", headers=_auth(owner))
        assert r.status_code == 200, r.text

    assert await _balance(session, buyer) == Decimal("100000")
    ledger = await _ledger(session, buyer)
    assert len(ledger) == 1 and ledger[0].amount == Decimal("100000")
    assert jobs == []


async def test_admin_may_credit_what_actually_arrived(client, session, jobs):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)
    topup_id = (await _topup(client, buyer)).json()["data"]["id"]

    r = await client.post(
        f"{A}/topups/{topup_id}/approve", json={"amount": "90000"}, headers=_auth(owner)
    )
    assert r.status_code == 200
    assert r.json()["data"]["credited_amount"] == "90000"
    assert await _balance(session, buyer) == Decimal("90000")


async def test_a_rejected_topup_credits_nothing(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)
    topup_id = (await _topup(client, buyer)).json()["data"]["id"]

    r = await client.post(
        f"{A}/topups/{topup_id}/reject",
        json={"reason": "Not received"},
        headers=_auth(owner),
    )
    assert r.status_code == 200
    r = await client.post(f"{A}/topups/{topup_id}/approve", headers=_auth(owner))
    assert r.status_code == 409
    assert await _balance(session, buyer) == 0
    assert await _ledger(session, buyer) == []


async def test_the_same_receipt_cannot_be_filed_twice(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    a = await _user(session, "a")
    b = await _user(session, "b")
    await _settings(client, owner)

    assert (await _topup(client, a, reference="REF-1234")).status_code == 201
    r = await _topup(client, b, reference="ref-1234")  # case differs, same receipt
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TOPUP_REFERENCE_USED"


async def test_a_corrected_receipt_can_be_refiled_after_rejection(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)
    topup_id = (await _topup(client, buyer, reference="555555")).json()["data"]["id"]
    await client.post(
        f"{A}/topups/{topup_id}/reject", json={"reason": "typo"}, headers=_auth(owner)
    )
    assert (await _topup(client, buyer, reference="555555")).status_code == 201


async def test_amount_limits_and_pending_cap(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)

    r = await _topup(client, buyer, amount="5000")
    assert r.json()["error"]["code"] == "TOPUP_AMOUNT_OUT_OF_RANGE"

    for i in range(3):
        assert (await _topup(client, buyer, reference=f"10000{i}")).status_code == 201
    r = await _topup(client, buyer, reference="100009")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "TOO_MANY_PENDING_TOPUPS"


async def test_a_disabled_method_is_refused(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner, card={"enabled": False})
    r = await _topup(client, buyer)
    assert r.json()["error"]["code"] == "PAYMENT_METHOD_DISABLED"


async def test_crypto_topup_quotes_the_amount_to_send(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)

    r = await client.post(
        f"{API}/wallet/topups",
        json={
            "method": "CRYPTO",
            "amount": "100000",
            "reference": TX_HASH,
            "network": "TRC20",
            "asset": "USDT",
        },
        headers=_auth(buyer),
    )
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    # 100000 / 60000 = 1.6666…, rounded up so the customer never sends short.
    assert Decimal(data["crypto_amount"]) == Decimal("1.666667")

    stored = await session.get(TopUp, data["id"])
    assert stored.destination == TRC20

    # An address pasted where the hash belongs is caught.
    r = await client.post(
        f"{API}/wallet/topups",
        json={
            "method": "CRYPTO",
            "amount": "100000",
            "reference": TRC20,
            "network": "TRC20",
        },
        headers=_auth(buyer),
    )
    assert r.status_code == 422


async def test_a_customer_can_cancel_only_their_own_pending_topup(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    other = await _user(session, "other")
    await _settings(client, owner)
    topup_id = (await _topup(client, buyer)).json()["data"]["id"]

    r = await client.post(f"{API}/wallet/topups/{topup_id}/cancel", headers=_auth(other))
    assert r.status_code == 404
    r = await client.post(f"{API}/wallet/topups/{topup_id}/cancel", headers=_auth(buyer))
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "CANCELLED"


# ----------------------------------------------------------------- purchase
async def test_paying_an_order_from_the_wallet(client, session, jobs):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)
    order = await _pending_order(session, buyer, "50000")

    r = await client.post(f"{API}/orders/{order.id}/pay", headers=_auth(buyer))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "INSUFFICIENT_BALANCE"

    topup_id = (await _topup(client, buyer, amount="80000")).json()["data"]["id"]
    await client.post(f"{A}/topups/{topup_id}/approve", headers=_auth(owner))

    for _ in range(2):  # paying twice charges once and provisions once
        r = await client.post(f"{API}/orders/{order.id}/pay", headers=_auth(buyer))
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "PAID"

    assert await _balance(session, buyer) == Decimal("30000")
    kinds = sorted(t.kind.value for t in await _ledger(session, buyer))
    assert kinds == ["PURCHASE", "TOPUP"]
    assert jobs == [("create_panel_user", {"order_id": order.id})]


async def test_someone_elses_order_cannot_be_paid(client, session, jobs):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    thief = await _user(session, "thief")
    order = await _pending_order(session, buyer)
    await client.post(
        f"{A}/users/{thief.id}/wallet/adjust",
        json={"amount": "999999", "note": "test"},
        headers=_auth(owner),
    )
    r = await client.post(f"{API}/orders/{order.id}/pay", headers=_auth(thief))
    assert r.status_code == 404
    assert await _balance(session, thief) == Decimal("999999")


async def test_approving_a_topup_for_an_order_pays_it(client, session, jobs):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)
    order = await _pending_order(session, buyer, "50000")

    r = await _topup(client, buyer, amount="50000", order_id=order.id)
    topup_id = r.json()["data"]["id"]
    r = await client.post(f"{A}/topups/{topup_id}/approve", headers=_auth(owner))
    assert r.json()["data"]["paid_order_id"] == order.id

    await session.refresh(order)
    assert order.status is OrderStatus.PAID
    assert await _balance(session, buyer) == 0
    assert jobs == [("create_panel_user", {"order_id": order.id})]


async def test_an_underpaid_topup_for_an_order_leaves_it_unpaid(client, session, jobs):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)
    order = await _pending_order(session, buyer, "50000")
    topup_id = (await _topup(client, buyer, amount="50000", order_id=order.id)).json()[
        "data"
    ]["id"]

    r = await client.post(
        f"{A}/topups/{topup_id}/approve", json={"amount": "40000"}, headers=_auth(owner)
    )
    assert r.json()["data"]["paid_order_id"] is None
    await session.refresh(order)
    assert order.status is OrderStatus.PENDING
    assert await _balance(session, buyer) == Decimal("40000")
    assert jobs == []


# --------------------------------------------------------------- adjustment
async def test_adjustments_cannot_overdraw(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    url = f"{A}/users/{buyer.id}/wallet/adjust"

    r = await client.post(
        url, json={"amount": "20000", "note": "gift"}, headers=_auth(owner)
    )
    assert r.json()["data"]["balance"] == "20000.00"
    r = await client.post(
        url, json={"amount": "-30000", "note": "x"}, headers=_auth(owner)
    )
    assert r.status_code == 422
    assert await _balance(session, buyer) == Decimal("20000")


async def test_support_cannot_touch_money(client, session):
    support = await _user(session, "sup", AdminRole.SUPPORT)
    buyer = await _user(session, "buyer")
    r = await client.post(
        f"{A}/users/{buyer.id}/wallet/adjust",
        json={"amount": "1", "note": "x"},
        headers=_auth(support),
    )
    assert r.status_code == 403
    assert (await client.get(f"{A}/topups", headers=_auth(support))).status_code == 403


async def test_admin_queue_flags_a_reused_reference(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await _settings(client, owner)
    first = (await _topup(client, buyer, reference="777777")).json()["data"]["id"]
    await client.post(
        f"{A}/topups/{first}/reject", json={"reason": "dup"}, headers=_auth(owner)
    )
    await _topup(client, buyer, reference="777777")

    r = await client.get(f"{A}/topups?status=PENDING", headers=_auth(owner))
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["reference_seen"] == 2


async def test_wallet_endpoint_shows_balance_and_history(client, session):
    owner = await _user(session, "owner", AdminRole.OWNER)
    buyer = await _user(session, "buyer")
    await client.post(
        f"{A}/users/{buyer.id}/wallet/adjust",
        json={"amount": "15000", "note": "welcome"},
        headers=_auth(owner),
    )
    r = await client.get(f"{API}/wallet", headers=_auth(buyer))
    data = r.json()["data"]
    assert Decimal(data["balance"]) == Decimal("15000")
    assert data["transactions"][0]["kind"] == "ADJUSTMENT"
    count = await session.scalar(select(func.count()).select_from(User))
    assert count == 2
