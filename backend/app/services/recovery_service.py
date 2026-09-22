"""Password reset and email verification.

The whole design is shaped by one rule: **this must not become a way to
discover whether an address is registered.** So the forgot-password endpoint
answers identically whether or not the account exists, and does the same
amount of visible work either way.

The second rule: a reset link is a credential for its lifetime. It is stored
hashed, single-use, short-lived, and using it revokes every existing session —
because a reset is what someone does when they think their account is
compromised.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import TokenInvalidError, ValidationError
from app.core.security import (
    generate_opaque_token,
    hash_password,
    hash_token,
)
from app.models.enums import UserStatus
from app.models.user import RefreshToken, User, VerificationToken
from app.services.mailer import (
    MailTransport,
    build_mailer,
    email_verification_message,
    password_reset_message,
)

logger = logging.getLogger(__name__)

PURPOSE_RESET = "password_reset"
PURPOSE_VERIFY = "email_verify"

# A user may hold only so many live reset tokens; beyond that, requesting more
# is either a mistake or an attempt to fill the table.
MAX_LIVE_TOKENS = 5


class RecoveryService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        mailer: MailTransport | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.mailer = mailer or build_mailer(self.settings)

    # ------------------------------------------------------- forgot password
    async def request_password_reset(
        self, identifier: str, *, ip_address: str | None = None
    ) -> None:
        """Send a reset link if the identifier matches an account.

        Returns nothing in every case. The caller answers 200 regardless, so
        the response cannot be used to enumerate registered addresses.
        """
        identifier = identifier.strip().lower()
        user = await self.session.scalar(
            select(User).where(
                or_(
                    User.username == identifier,
                    User.email == identifier,
                    User.phone == identifier,
                )
            )
        )

        if user is None or user.status is UserStatus.BANNED:
            logger.info(
                "password_reset_requested_unknown",
                extra={"extra_fields": {"ip": ip_address}},
            )
            return

        if not user.email:
            # Nothing to send to. Phone-based reset needs an SMS transport,
            # which is not configured (see docs/security.md).
            logger.info(
                "password_reset_no_email",
                extra={"extra_fields": {"user_id": user.id}},
            )
            return

        live = await self._live_token_count(user.id, PURPOSE_RESET)
        if live >= MAX_LIVE_TOKENS:
            logger.warning(
                "password_reset_throttled",
                extra={"extra_fields": {"user_id": user.id}},
            )
            return

        raw_token = generate_opaque_token(32)
        ttl = self.settings.reset_token_ttl_minutes
        self.session.add(
            VerificationToken(
                user_id=user.id,
                purpose=PURPOSE_RESET,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(minutes=ttl),
                ip_address=ip_address,
            )
        )
        await self.session.flush()

        base = self.settings.password_reset_url or (
            f"{self.settings.public_api_url.rstrip('/')}/reset-password"
        )
        await self.mailer.send(
            password_reset_message(user.email, f"{base}?token={raw_token}", ttl)
        )
        logger.info("password_reset_sent", extra={"extra_fields": {"user_id": user.id}})

    # -------------------------------------------------------- reset password
    async def reset_password(self, raw_token: str, new_password: str) -> User:
        """Consume a reset token and set a new password.

        Every session is revoked afterwards. A reset usually means the account
        was compromised, and leaving the attacker's refresh token alive would
        defeat the point.
        """
        record = await self._consume(raw_token, PURPOSE_RESET)
        user = await self.session.get(User, record.user_id)
        if user is None:
            raise TokenInvalidError("Token is invalid")

        if user.status is UserStatus.BANNED:
            raise TokenInvalidError("Token is invalid")

        user.password_hash = hash_password(new_password)

        await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC), revoked_reason="password_reset")
        )

        # Any other live reset tokens die too, so a second stolen link is dead.
        await self.session.execute(
            update(VerificationToken)
            .where(
                VerificationToken.user_id == user.id,
                VerificationToken.purpose == PURPOSE_RESET,
                VerificationToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(UTC))
        )

        await self.session.flush()
        logger.info(
            "password_reset_completed", extra={"extra_fields": {"user_id": user.id}}
        )
        return user

    # ---------------------------------------------------- email verification
    async def request_email_verification(self, user: User) -> None:
        if not user.email or user.is_email_verified:
            return

        live = await self._live_token_count(user.id, PURPOSE_VERIFY)
        if live >= MAX_LIVE_TOKENS:
            return

        raw_token = generate_opaque_token(32)
        ttl = self.settings.verify_token_ttl_hours
        self.session.add(
            VerificationToken(
                user_id=user.id,
                purpose=PURPOSE_VERIFY,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(hours=ttl),
            )
        )
        await self.session.flush()

        base = f"{self.settings.public_api_url.rstrip('/')}/verify"
        await self.mailer.send(
            email_verification_message(user.email, f"{base}?token={raw_token}", ttl)
        )

    async def verify_email(self, raw_token: str) -> User:
        record = await self._consume(raw_token, PURPOSE_VERIFY)
        user = await self.session.get(User, record.user_id)
        if user is None:
            raise TokenInvalidError("Token is invalid")

        user.is_email_verified = True
        await self.session.flush()
        return user

    # -------------------------------------------------------------- internals
    async def _live_token_count(self, user_id: str, purpose: str) -> int:
        now = datetime.now(UTC)
        rows = await self.session.scalars(
            select(VerificationToken).where(
                VerificationToken.user_id == user_id,
                VerificationToken.purpose == purpose,
                VerificationToken.used_at.is_(None),
            )
        )
        return sum(1 for r in rows if self._expires_at(r) > now)

    async def _consume(self, raw_token: str, purpose: str) -> VerificationToken:
        if not raw_token or len(raw_token) < 16:
            raise TokenInvalidError("Token is invalid")

        record = await self.session.scalar(
            select(VerificationToken).where(
                VerificationToken.token_hash == hash_token(raw_token),
                VerificationToken.purpose == purpose,
            )
        )
        # One message for "never existed", "already used" and "expired": each
        # distinction would tell an attacker something about a token they hold.
        if record is None or record.used_at is not None:
            raise TokenInvalidError("This link is invalid or has already been used")
        if self._expires_at(record) <= datetime.now(UTC):
            raise TokenInvalidError("This link is invalid or has already been used")

        record.used_at = datetime.now(UTC)
        await self.session.flush()
        return record

    @staticmethod
    def _expires_at(record: VerificationToken) -> datetime:
        value = record.expires_at
        return value if value.tzinfo else value.replace(tzinfo=UTC)


def validate_new_password(password: str) -> str:
    """Shared with the schema, so the rules cannot drift apart."""
    from app.schemas.auth import _validate_password

    try:
        return _validate_password(password)
    except ValueError as exc:
        raise ValidationError(str(exc), code="WEAK_PASSWORD") from exc
