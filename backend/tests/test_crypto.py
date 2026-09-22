"""Password hashing, token signing and panel-credential encryption."""

from __future__ import annotations

import base64
import time

import pytest

from app.core.security import (
    CredentialCipher,
    CredentialDecryptionError,
    TokenError,
    create_token,
    decode_token,
    hash_password,
    hash_token,
    tokens_match,
    verify_password,
)

KEY_A = base64.urlsafe_b64encode(b"a" * 32).decode()
KEY_B = base64.urlsafe_b64encode(b"b" * 32).decode()


# --- passwords --------------------------------------------------------------
def test_password_hash_is_not_the_password():
    hashed = hash_password("CorrectHorse1")
    assert "CorrectHorse1" not in hashed
    assert hashed.startswith("$argon2id$")


def test_password_verification_round_trip():
    hashed = hash_password("CorrectHorse1")
    assert verify_password("CorrectHorse1", hashed)
    assert not verify_password("correcthorse1", hashed)


def test_same_password_hashes_differently_each_time():
    """A per-password salt means identical passwords are not linkable."""
    assert hash_password("CorrectHorse1") != hash_password("CorrectHorse1")


def test_verify_does_not_raise_on_a_malformed_hash():
    assert verify_password("anything", "not-a-hash") is False


# --- tokens -----------------------------------------------------------------
def test_access_token_round_trip(settings):
    token, expires_at, jti = create_token("user-1", "access", settings=settings)
    claims = decode_token(token, "access", settings=settings)
    assert claims["sub"] == "user-1"
    assert claims["typ"] == "access"
    assert claims["jti"] == jti
    assert expires_at.timestamp() == pytest.approx(claims["exp"], abs=2)


def test_refresh_token_cannot_be_used_as_an_access_token(settings):
    """The two token kinds are signed with different secrets."""
    refresh, _, _ = create_token("user-1", "refresh", settings=settings)
    with pytest.raises(TokenError):
        decode_token(refresh, "access", settings=settings)


def test_access_token_cannot_be_used_as_a_refresh_token(settings):
    access, _, _ = create_token("user-1", "access", settings=settings)
    with pytest.raises(TokenError):
        decode_token(access, "refresh", settings=settings)


def test_tampered_token_is_rejected(settings):
    token, _, _ = create_token("user-1", "access", settings=settings)
    head, payload, sig = token.split(".")
    with pytest.raises(TokenError):
        decode_token(f"{head}.{payload}.{sig[:-2]}xx", "access", settings=settings)


def test_expired_token_is_rejected(settings):
    expired = settings.model_copy(update={"access_token_expire_minutes": -1})
    token, _, _ = create_token("user-1", "access", settings=expired)
    time.sleep(0.01)
    with pytest.raises(TokenError):
        decode_token(token, "access", settings=expired)


def test_tokens_are_stored_hashed_not_raw(settings):
    token, _, _ = create_token("user-1", "refresh", settings=settings)
    stored = hash_token(token)
    assert token not in stored
    assert len(stored) == 64
    assert tokens_match(token, stored)
    assert not tokens_match(token + "x", stored)


# --- panel credential encryption -------------------------------------------
def test_credentials_round_trip():
    cipher = CredentialCipher(KEY_A)
    encrypted = cipher.encrypt("panel-admin-password")
    assert encrypted is not None
    assert "panel-admin-password" not in encrypted
    assert cipher.decrypt(encrypted) == "panel-admin-password"


def test_encryption_is_non_deterministic():
    """Equal plaintexts must not produce equal ciphertexts."""
    cipher = CredentialCipher(KEY_A)
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_a_different_key_cannot_decrypt():
    encrypted = CredentialCipher(KEY_A).encrypt("panel-admin-password")
    with pytest.raises(CredentialDecryptionError):
        CredentialCipher(KEY_B).decrypt(encrypted)


def test_tampered_ciphertext_is_rejected():
    """Fernet authenticates, so a flipped byte fails rather than decrypting."""
    cipher = CredentialCipher(KEY_A)
    encrypted = cipher.encrypt("panel-admin-password")
    assert encrypted is not None
    tampered = encrypted[:-4] + ("AAAA" if encrypted[-4:] != "AAAA" else "BBBB")
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(tampered)


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_values_pass_through_as_none(empty):
    cipher = CredentialCipher(KEY_A)
    assert cipher.encrypt(empty) is None
    assert cipher.decrypt(empty) is None
