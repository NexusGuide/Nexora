"""Cryptographic primitives: password hashing, token signing, credential
encryption.

Nothing here reads a hardcoded key. All key material comes from
``Settings``, which itself comes from the environment.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings, get_settings

TokenType = Literal["access", "refresh"]

# Argon2id with parameters suited to an API server: memory-hard enough to make
# offline cracking expensive, fast enough not to become a login bottleneck.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


# --------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    """Return an Argon2id hash. The plain password is never stored or logged."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish verification that never raises on a bad hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ------------------------------------------------------------------------ tokens
class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or of the wrong type."""


def _secret_for(token_type: TokenType, settings: Settings) -> str:
    # "access"/"refresh" name a token KIND, not a credential (ruff S105).
    if token_type == "access":  # noqa: S105
        return settings.jwt_secret
    return settings.jwt_refresh_secret


def create_token(
    subject: str,
    token_type: TokenType,
    *,
    settings: Settings | None = None,
    extra_claims: dict[str, Any] | None = None,
    family_id: str | None = None,
) -> tuple[str, datetime, str]:
    """Sign a JWT.

    Returns ``(token, expires_at, jti)``. Access and refresh tokens are signed
    with *different* secrets, so a refresh token can never be replayed as an
    access token even if the verification path is confused.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)
    lifetime = (
        timedelta(minutes=settings.access_token_expire_minutes)
        if token_type == "access"  # noqa: S105
        else timedelta(days=settings.refresh_token_expire_days)
    )
    expires_at = now + lifetime
    jti = uuid.uuid4().hex

    claims: dict[str, Any] = {
        "sub": subject,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": jti,
        "iss": settings.app_name,
    }
    if family_id:
        claims["fam"] = family_id
    if extra_claims:
        claims.update(extra_claims)

    token = jwt.encode(
        claims, _secret_for(token_type, settings), algorithm=settings.jwt_algorithm
    )
    return token, expires_at, jti


def decode_token(
    token: str, token_type: TokenType, *, settings: Settings | None = None
) -> dict[str, Any]:
    """Verify a JWT and return its claims, or raise :class:`TokenError`.

    The ``typ`` claim is checked explicitly: a token of the wrong kind is
    rejected even when the signature is valid.
    """
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            _secret_for(token_type, settings),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_name,
            options={"require": ["exp", "iat", "sub", "jti", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid") from exc

    if claims.get("typ") != token_type:
        raise TokenError("Token is of the wrong type")
    return claims


def hash_token(token: str) -> str:
    """SHA-256 of a token, for storage.

    Refresh tokens are stored hashed so that a database leak does not yield
    usable sessions.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(token: str, stored_hash: str) -> bool:
    """Timing-safe comparison of a presented token against a stored hash."""
    return hmac.compare_digest(hash_token(token), stored_hash)


def generate_opaque_token(length: int = 48) -> str:
    """A URL-safe random token for one-off uses (verification, reset links)."""
    return secrets.token_urlsafe(length)


# ------------------------------------------------------------------- encryption
class CredentialCipher:
    """Encrypts panel credentials at rest.

    Uses Fernet (AES-128-CBC + HMAC-SHA256, with a timestamp and random IV per
    message), keyed by ``ENCRYPTION_KEY``. The key exists only in the
    environment; it is never written to the database, the repository or a log.

    Losing the key makes stored credentials unreadable — see SECURITY.md.
    """

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> CredentialCipher:
        return cls((settings or get_settings()).encryption_key)

    def encrypt(self, plaintext: str | None) -> str | None:
        """Encrypt a value. ``None`` and empty strings pass through as ``None``."""
        if not plaintext:
            return None
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str | None) -> str | None:
        """Decrypt a value, or raise :class:`CredentialDecryptionError`."""
        if not ciphertext:
            return None
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise CredentialDecryptionError(
                "Stored credential could not be decrypted. The ENCRYPTION_KEY "
                "does not match the one used to encrypt it."
            ) from exc


class CredentialDecryptionError(Exception):
    """Raised when ciphertext cannot be decrypted with the configured key."""
