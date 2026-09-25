"""Registration, login lockout and refresh-token rotation."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    TokenInvalidError,
)
from app.models.enums import UserStatus
from app.models.user import RefreshToken, User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.auth_service import AuthService

PASSWORD = "CorrectHorse1"


async def register_user(session, username="alice", **kw) -> User:
    service = AuthService(session)
    user = await service.register(
        RegisterRequest(username=username, password=PASSWORD, **kw)
    )
    await session.commit()
    return user


# --- registration -----------------------------------------------------------
async def test_register_stores_a_hash_not_the_password(session):
    user = await register_user(session)
    assert user.password_hash != PASSWORD
    assert PASSWORD not in user.password_hash
    assert user.status is UserStatus.ACTIVE


async def test_duplicate_username_is_rejected(session):
    await register_user(session)
    with pytest.raises(ConflictError):
        await register_user(session)


async def test_duplicate_email_does_not_reveal_which_field_matched(session):
    await register_user(session, email="a@example.com")
    with pytest.raises(ConflictError) as exc:
        await register_user(session, username="bob", email="a@example.com")
    message = str(exc.value).lower()
    assert "email" not in message and "username" not in message


# --- login ------------------------------------------------------------------
async def test_login_succeeds_and_issues_a_token_pair(session):
    await register_user(session)
    service = AuthService(session)
    result = await service.login(
        LoginRequest(identifier="alice", password=PASSWORD), ip_address="1.2.3.4"
    )
    assert result.tokens.access_token
    assert result.tokens.refresh_token
    assert result.tokens.access_token != result.tokens.refresh_token
    assert result.user.username == "alice"


async def test_login_response_carries_no_password_field(session):
    await register_user(session)
    service = AuthService(session)
    result = await service.login(LoginRequest(identifier="alice", password=PASSWORD))
    dumped = result.model_dump_json()
    assert PASSWORD not in dumped
    assert "password" not in dumped


async def test_unknown_user_and_wrong_password_fail_identically(session):
    await register_user(session)
    service = AuthService(session)

    with pytest.raises(AuthenticationError) as unknown:
        await service.login(LoginRequest(identifier="nobody", password=PASSWORD))
    with pytest.raises(AuthenticationError) as wrong:
        await service.login(LoginRequest(identifier="alice", password="WrongPass1"))

    assert str(unknown.value) == str(wrong.value)


async def test_refresh_token_is_stored_hashed(session):
    await register_user(session)
    service = AuthService(session)
    result = await service.login(LoginRequest(identifier="alice", password=PASSWORD))
    await session.commit()

    stored = await session.scalar(select(RefreshToken))
    assert stored is not None
    assert stored.token_hash != result.tokens.refresh_token
    assert len(stored.token_hash) == 64


async def test_suspended_account_cannot_log_in(session):
    user = await register_user(session)
    user.status = UserStatus.SUSPENDED
    await session.commit()

    with pytest.raises(AccountInactiveError):
        await AuthService(session).login(
            LoginRequest(identifier="alice", password=PASSWORD)
        )


async def test_lockout_after_repeated_failures(session, settings):
    await register_user(session)
    service = AuthService(session)

    for _ in range(settings.login_max_attempts):
        with pytest.raises(AuthenticationError):
            await service.login(
                LoginRequest(identifier="alice", password="WrongPass1"),
                ip_address="1.2.3.4",
            )

    # The correct password is now refused too — lockout is on the account.
    with pytest.raises(AccountLockedError):
        await service.login(
            LoginRequest(identifier="alice", password=PASSWORD), ip_address="1.2.3.4"
        )


# --- refresh rotation -------------------------------------------------------
async def test_refresh_rotates_the_token(session):
    await register_user(session)
    service = AuthService(session)
    first = await service.login(LoginRequest(identifier="alice", password=PASSWORD))
    await session.commit()

    second = await service.refresh(first.tokens.refresh_token)
    await session.commit()

    assert second.refresh_token != first.tokens.refresh_token
    assert second.access_token != first.tokens.access_token


async def test_reusing_a_rotated_token_revokes_the_whole_family(session):
    """Detected reuse means the token leaked, so every session dies."""
    await register_user(session)
    service = AuthService(session)
    first = await service.login(LoginRequest(identifier="alice", password=PASSWORD))
    await session.commit()

    second = await service.refresh(first.tokens.refresh_token)
    await session.commit()

    with pytest.raises(TokenInvalidError) as exc:
        await service.refresh(first.tokens.refresh_token)
    await session.commit()
    assert exc.value.code == "TOKEN_REUSE_DETECTED"

    # The token issued by the legitimate rotation is revoked as well.
    with pytest.raises(TokenInvalidError):
        await service.refresh(second.refresh_token)


async def test_an_unknown_refresh_token_is_rejected(session, settings):
    from app.core.security import create_token

    await register_user(session)
    forged, _, _ = create_token("ghost", "refresh", settings=settings)
    with pytest.raises(TokenInvalidError):
        await AuthService(session).refresh(forged)


async def test_logout_all_revokes_every_session(session):
    await register_user(session)
    service = AuthService(session)
    user = await session.scalar(select(User).where(User.username == "alice"))
    assert user is not None

    a = await service.login(
        LoginRequest(identifier="alice", password=PASSWORD, device_id="phone")
    )
    await service.login(
        LoginRequest(identifier="alice", password=PASSWORD, device_id="tablet")
    )
    await session.commit()

    await service.logout(user.id, refresh_token=None, all_devices=True)
    await session.commit()

    active = await session.scalar(
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.revoked_at.is_(None))
    )
    assert active == 0
    with pytest.raises(TokenInvalidError):
        await service.refresh(a.tokens.refresh_token)


# --- email case -------------------------------------------------------------
# A phone keyboard capitalises the first letter of an email field on its own.
# Found on the first real sign-up: registered as "Mehdi…", signed in as
# "mehdi…", and was refused.
async def test_email_is_stored_lowercase(session):
    user = await register_user(session, email="Mixed.Case@Example.com")
    assert user.email == "mixed.case@example.com"


async def test_sign_in_with_email_ignores_case(session):
    await register_user(session, email="Mixed.Case@Example.com")
    service = AuthService(session)
    for typed in ("mixed.case@example.com", "MIXED.CASE@EXAMPLE.COM"):
        result = await service.login(LoginRequest(identifier=typed, password=PASSWORD))
        assert result.user.username == "alice"


async def test_an_address_stored_before_normalisation_still_signs_in(session):
    # Rows written before emails were lowercased keep their original case.
    user = await register_user(session)
    user.email = "Legacy.User@Example.com"
    await session.commit()
    result = await AuthService(session).login(
        LoginRequest(identifier="legacy.user@example.com", password=PASSWORD)
    )
    assert result.user.id == user.id


async def test_duplicate_email_is_caught_regardless_of_case(session):
    await register_user(session, email="dup@example.com")
    with pytest.raises(ConflictError):
        await register_user(session, username="bob", email="DUP@Example.com")
