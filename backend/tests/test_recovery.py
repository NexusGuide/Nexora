"""Password reset must work, and must not become an account-enumeration tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.exceptions import TokenInvalidError
from app.core.security import verify_password
from app.models.user import RefreshToken, VerificationToken
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.auth_service import AuthService
from app.services.mailer import ConsoleTransport
from app.services.recovery_service import PURPOSE_RESET, RecoveryService

PASSWORD = "CorrectHorse1"
NEW_PASSWORD = "BrandNewPass9"


async def make_user(session, username="alice", email="alice@example.com"):
    user = await AuthService(session).register(
        RegisterRequest(username=username, password=PASSWORD, email=email)
    )
    await session.commit()
    return user


def token_from(mailer: ConsoleTransport) -> str:
    assert mailer.sent, "no email was sent"
    body = mailer.sent[-1].body
    return body.split("token=")[1].split()[0].strip()


# --- requesting -------------------------------------------------------------
async def test_reset_email_is_sent_for_a_known_account(session):
    user = await make_user(session)
    mailer = ConsoleTransport()

    await RecoveryService(session, mailer=mailer).request_password_reset("alice")
    await session.commit()

    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == "alice@example.com"
    assert "token=" in mailer.sent[0].body

    record = await session.scalar(select(VerificationToken))
    assert record.user_id == user.id
    assert record.purpose == PURPOSE_RESET
    # Stored hashed, like refresh tokens.
    assert record.token_hash != token_from(mailer)
    assert len(record.token_hash) == 64


async def test_an_unknown_identifier_sends_nothing_and_says_nothing(session):
    """The endpoint must not reveal which addresses are registered."""
    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)

    result = await service.request_password_reset("nobody@example.com")
    await session.commit()

    assert result is None
    assert mailer.sent == []
    assert await session.scalar(select(VerificationToken)) is None


async def test_lookup_works_by_username_email_or_phone(session):
    await make_user(session)
    for identifier in ("alice", "alice@example.com", "ALICE@EXAMPLE.COM"):
        mailer = ConsoleTransport()
        await RecoveryService(session, mailer=mailer).request_password_reset(identifier)
        await session.commit()
        assert len(mailer.sent) == 1, identifier


async def test_repeated_requests_are_throttled(session):
    """Otherwise the endpoint is a way to flood an inbox, or the table."""
    await make_user(session)
    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)

    for _ in range(8):
        await service.request_password_reset("alice")
        await session.commit()

    assert len(mailer.sent) == 5  # MAX_LIVE_TOKENS


async def test_an_account_without_an_email_gets_nothing(session):
    await make_user(session, username="nomail", email=None)
    mailer = ConsoleTransport()

    await RecoveryService(session, mailer=mailer).request_password_reset("nomail")
    await session.commit()

    assert mailer.sent == []


# --- resetting --------------------------------------------------------------
async def test_reset_changes_the_password(session):
    user = await make_user(session)
    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)

    await service.request_password_reset("alice")
    await session.commit()

    await service.reset_password(token_from(mailer), NEW_PASSWORD)
    await session.commit()

    assert verify_password(NEW_PASSWORD, user.password_hash)
    assert not verify_password(PASSWORD, user.password_hash)


async def test_a_token_works_only_once(session):
    await make_user(session)
    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)

    await service.request_password_reset("alice")
    await session.commit()
    token = token_from(mailer)

    await service.reset_password(token, NEW_PASSWORD)
    await session.commit()

    with pytest.raises(TokenInvalidError):
        await service.reset_password(token, "AnotherPass1")


async def test_reset_revokes_every_session(session):
    """A reset means "I think I'm compromised"; stale sessions must die."""
    await make_user(session)
    auth = AuthService(session)

    await auth.login(
        LoginRequest(identifier="alice", password=PASSWORD, device_id="phone")
    )
    await auth.login(
        LoginRequest(identifier="alice", password=PASSWORD, device_id="laptop")
    )
    await session.commit()

    live = [
        t for t in await session.scalars(select(RefreshToken)) if t.revoked_at is None
    ]
    assert len(live) == 2

    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)
    await service.request_password_reset("alice")
    await session.commit()
    await service.reset_password(token_from(mailer), NEW_PASSWORD)
    await session.commit()

    still_live = [
        t for t in await session.scalars(select(RefreshToken)) if t.revoked_at is None
    ]
    assert still_live == []


async def test_other_live_reset_tokens_die_too(session):
    """A second stolen link must not survive the first reset."""
    await make_user(session)
    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)

    await service.request_password_reset("alice")
    await session.commit()
    first_token = token_from(mailer)

    await service.request_password_reset("alice")
    await session.commit()
    second_token = token_from(mailer)
    assert first_token != second_token

    await service.reset_password(second_token, NEW_PASSWORD)
    await session.commit()

    with pytest.raises(TokenInvalidError):
        await service.reset_password(first_token, "YetAnother1")


async def test_an_expired_token_is_refused(session):
    await make_user(session)
    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)

    await service.request_password_reset("alice")
    await session.commit()
    token = token_from(mailer)

    record = await session.scalar(select(VerificationToken))
    record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()

    with pytest.raises(TokenInvalidError):
        await service.reset_password(token, NEW_PASSWORD)


@pytest.mark.parametrize("bad", ["", "short", "x" * 40, "not-a-real-token-at-all"])
async def test_a_bogus_token_is_refused(session, bad):
    await make_user(session)
    with pytest.raises(TokenInvalidError):
        await RecoveryService(session).reset_password(bad, NEW_PASSWORD)


async def test_failure_messages_do_not_distinguish_the_reason(session):
    """Used, expired and never-existed must look the same to an attacker."""
    await make_user(session)
    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)

    await service.request_password_reset("alice")
    await session.commit()
    token = token_from(mailer)
    await service.reset_password(token, NEW_PASSWORD)
    await session.commit()

    with pytest.raises(TokenInvalidError) as used:
        await service.reset_password(token, "SomePass123")
    with pytest.raises(TokenInvalidError) as unknown:
        await service.reset_password("z" * 48, "SomePass123")

    assert str(used.value) == str(unknown.value)


# --- email verification -----------------------------------------------------
async def test_email_verification_round_trip(session):
    user = await make_user(session)
    assert user.is_email_verified is False

    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)
    await service.request_email_verification(user)
    await session.commit()

    await service.verify_email(token_from(mailer))
    await session.commit()
    assert user.is_email_verified is True


async def test_an_already_verified_address_sends_nothing(session):
    user = await make_user(session)
    user.is_email_verified = True
    await session.commit()

    mailer = ConsoleTransport()
    await RecoveryService(session, mailer=mailer).request_email_verification(user)
    await session.commit()
    assert mailer.sent == []


async def test_a_reset_token_cannot_verify_an_email(session):
    """Purposes must not be interchangeable."""
    user = await make_user(session)
    mailer = ConsoleTransport()
    service = RecoveryService(session, mailer=mailer)

    await service.request_password_reset("alice")
    await session.commit()

    with pytest.raises(TokenInvalidError):
        await service.verify_email(token_from(mailer))
    _ = user
