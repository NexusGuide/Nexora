"""The bootstrap endpoint grants total control of the deployment, so it is
tested at the HTTP layer rather than through the service beneath it — what
matters is exactly what a caller on the Internet can and cannot do with it.

The three properties that make it safe rather than a back door:

  1. it closes permanently once any administrator exists;
  2. it elevates an existing account and never creates one;
  3. a wrong secret is refused, in constant time.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.enums import AdminRole
from app.models.user import User

SECRET = "9f8e7d6c5b4a39281706f5e4d3c2b1a0-admin-for-tests-only"
BOOTSTRAP = "/api/v1/admin/bootstrap"


@pytest.fixture
async def client(session):
    """The real app, with the database swapped for the per-test session."""
    from app.api.deps import get_session
    from app.main import app

    async def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override
    # base_url must be an allowed host: TrustedHostMiddleware is part of the
    # stack under test, and "testserver" is not in ALLOWED_HOSTS.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c
    app.dependency_overrides.clear()


async def _make_user(session, username="rend", email="rend@example.com", role=None):
    user = User(
        username=username,
        email=email,
        password_hash=hash_password("Str0ng-Passw0rd-for-tests-only"),
        admin_role=role,
    )
    session.add(user)
    await session.flush()
    return user


# --- the happy path ---------------------------------------------------------


async def test_promotes_an_existing_account_to_owner(client, session):
    user = await _make_user(session)
    assert user.admin_role is None

    r = await client.post(BOOTSTRAP, json={"secret": SECRET, "identifier": "rend"})

    assert r.status_code == 200, r.text
    assert r.json()["data"]["admin_role"] == "OWNER"
    await session.refresh(user)
    assert user.admin_role is AdminRole.OWNER


async def test_accepts_the_email_as_the_identifier(client, session):
    user = await _make_user(session)

    r = await client.post(
        BOOTSTRAP, json={"secret": SECRET, "identifier": "rend@example.com"}
    )

    assert r.status_code == 200, r.text
    await session.refresh(user)
    assert user.admin_role is AdminRole.OWNER


# --- the property that stops it being a back door ---------------------------


async def test_closes_permanently_once_an_administrator_exists(client, session):
    await _make_user(session, role=AdminRole.OWNER)
    await _make_user(session, username="second", email="second@example.com")

    r = await client.post(BOOTSTRAP, json={"secret": SECRET, "identifier": "second"})

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


async def test_a_second_call_is_refused(client, session):
    await _make_user(session)

    first = await client.post(BOOTSTRAP, json={"secret": SECRET, "identifier": "rend"})
    second = await client.post(BOOTSTRAP, json={"secret": SECRET, "identifier": "rend"})

    assert first.status_code == 200
    assert second.status_code == 409


async def test_a_closed_endpoint_does_not_reveal_whether_the_secret_was_right(
    client, session
):
    """Once closed it answers 409 to everything.

    If it checked the secret first, a late caller could tell a correct secret
    from a wrong one by the status code, turning a closed endpoint into an
    oracle for guessing it.
    """
    await _make_user(session, role=AdminRole.OWNER)
    await _make_user(session, username="second", email="second@example.com")

    right = await client.post(BOOTSTRAP, json={"secret": SECRET, "identifier": "second"})
    wrong = await client.post(
        BOOTSTRAP,
        json={"secret": "x" * 64, "identifier": "second"},
    )

    assert right.status_code == wrong.status_code == 409
    assert right.json()["error"] == wrong.json()["error"]


# --- refusals ---------------------------------------------------------------


async def test_a_wrong_secret_is_refused_and_grants_nothing(client, session):
    user = await _make_user(session)

    r = await client.post(BOOTSTRAP, json={"secret": "n" * 64, "identifier": "rend"})

    assert r.status_code == 403
    await session.refresh(user)
    assert user.admin_role is None


async def test_it_does_not_create_accounts(client, session):
    """An unknown identifier is a 404, not a new user."""
    r = await client.post(BOOTSTRAP, json={"secret": SECRET, "identifier": "ghost"})

    assert r.status_code == 404
    assert (await session.scalar(select(User).limit(1))) is None


async def test_a_short_secret_is_rejected_before_it_is_compared(client, session):
    await _make_user(session)

    r = await client.post(BOOTSTRAP, json={"secret": "short", "identifier": "rend"})

    # 422, but wrapped in the project's envelope rather than FastAPI's default
    # body, so a client parses this the same way as every other error.
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    # The rejected value must not come back: a bad body can contain a secret.
    assert "short" not in r.text
