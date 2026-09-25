"""Authentication service.

Holds the security-sensitive logic that endpoints must not reimplement:
credential verification, brute-force lockout, and refresh-token rotation with
reuse detection.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AccountInactiveError,
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    TokenInvalidError,
)
from app.core.security import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)
from app.models.enums import UserStatus
from app.models.user import LoginAttempt, RefreshToken, User
from app.schemas.auth import (
    AuthResult,
    LoginRequest,
    RegisterRequest,
    TokenPair,
    UserPublic,
)
from app.services.device_service import DeviceService

logger = logging.getLogger(__name__)

# A dummy Argon2 hash verified on unknown identifiers, so a failed login costs
# the same time whether or not the account exists (no timing oracle).
_DUMMY_HASH = hash_password("nexus-timing-equalizer-not-a-real-password")


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # ------------------------------------------------------------- register
    async def register(self, payload: RegisterRequest) -> User:
        clauses = [User.username == payload.username]
        if payload.email:
            clauses.append(func.lower(User.email) == str(payload.email).lower())
        if payload.phone:
            clauses.append(User.phone == payload.phone)

        existing = await self.session.scalar(select(User).where(or_(*clauses)))
        if existing is not None:
            # One message for all three collisions: telling the caller *which*
            # field matched would confirm that an email or phone is registered.
            raise ConflictError(
                "An account with these details already exists",
                code="ACCOUNT_EXISTS",
            )

        user = User(
            username=payload.username,
            email=str(payload.email) if payload.email else None,
            phone=payload.phone,
            password_hash=hash_password(payload.password),
            status=UserStatus.ACTIVE,
        )
        self.session.add(user)
        await self.session.flush()
        logger.info("user_registered", extra={"extra_fields": {"user_id": user.id}})
        return user

    # ---------------------------------------------------------------- login
    async def login(
        self,
        payload: LoginRequest,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResult:
        identifier = payload.identifier.strip().lower()
        await self._enforce_lockout(identifier, ip_address)

        user = await self.session.scalar(
            select(User).where(
                or_(
                    User.username == identifier,
                    func.lower(User.email) == identifier,
                    User.phone == payload.identifier.strip(),
                )
            )
        )

        if user is None:
            # Spend the same work as a real verification before failing.
            verify_password(payload.password, _DUMMY_HASH)
            await self._record_attempt(identifier, ip_address, False, user_agent)
            raise AuthenticationError()

        if not verify_password(payload.password, user.password_hash):
            await self._record_attempt(identifier, ip_address, False, user_agent)
            raise AuthenticationError()

        if user.status is not UserStatus.ACTIVE:
            await self._record_attempt(identifier, ip_address, False, user_agent)
            raise AccountInactiveError(
                f"This account is {user.status.value.lower()}",
                details={"status": user.status.value},
            )

        # Transparently upgrade a hash made with older parameters.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(payload.password)

        await self._record_attempt(identifier, ip_address, True, user_agent)
        user.last_login_at = datetime.now(UTC)

        if payload.device_id:
            await self._touch_device(user, payload)

        tokens = await self._issue_token_pair(
            user,
            family_id=uuid.uuid4().hex,
            device_id=payload.device_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self.session.flush()
        return AuthResult(user=UserPublic.model_validate(user), tokens=tokens)

    # -------------------------------------------------------------- refresh
    async def refresh(
        self,
        refresh_token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> TokenPair:
        """Rotate a refresh token.

        Reuse of an already-rotated token means the token leaked: the whole
        family is revoked, forcing every session derived from it to log in
        again.
        """
        try:
            claims = decode_token(refresh_token, "refresh", settings=self.settings)
        except TokenError as exc:
            raise TokenInvalidError(str(exc)) from exc

        stored = await self.session.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_token(refresh_token)
            )
        )
        if stored is None:
            raise TokenInvalidError("Refresh token is not recognised")

        if stored.used_at is not None or stored.revoked_at is not None:
            await self._revoke_family(stored.family_id, reason="reuse_detected")
            logger.warning(
                "refresh_token_reuse_detected",
                extra={
                    "extra_fields": {
                        "user_id": stored.user_id,
                        "family_id": stored.family_id,
                    }
                },
            )
            raise TokenInvalidError(
                "Refresh token has already been used. All sessions were revoked.",
                code="TOKEN_REUSE_DETECTED",
            )

        if stored.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
            raise TokenInvalidError("Refresh token has expired", code="TOKEN_EXPIRED")

        user = await self.session.get(User, stored.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            await self._revoke_family(stored.family_id, reason="user_inactive")
            raise AccountInactiveError()

        now = datetime.now(UTC)
        stored.used_at = now
        stored.revoked_at = now
        stored.revoked_reason = "rotated"

        tokens = await self._issue_token_pair(
            user,
            family_id=stored.family_id,
            device_id=stored.device_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self.session.flush()
        _ = claims  # verified above; retained for clarity
        return tokens

    # --------------------------------------------------------------- logout
    async def logout(
        self, user_id: str, *, refresh_token: str | None, all_devices: bool
    ) -> None:
        now = datetime.now(UTC)
        if all_devices:
            await self.session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=now, revoked_reason="logout_all")
            )
        elif refresh_token:
            stored = await self.session.scalar(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_token(refresh_token),
                    RefreshToken.user_id == user_id,
                )
            )
            if stored is not None:
                await self._revoke_family(stored.family_id, reason="logout")
        await self.session.flush()

    # ------------------------------------------------------------- internals
    async def _issue_token_pair(
        self,
        user: User,
        *,
        family_id: str,
        device_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> TokenPair:
        access, _, _ = create_token(
            user.id,
            "access",
            settings=self.settings,
            extra_claims={"role": user.admin_role} if user.admin_role else None,
        )
        refresh, refresh_exp, refresh_jti = create_token(
            user.id, "refresh", settings=self.settings, family_id=family_id
        )

        self.session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_token(refresh),
                jti=refresh_jti,
                family_id=family_id,
                device_id=device_id,
                expires_at=refresh_exp,
                ip_address=ip_address,
                user_agent=(user_agent or "")[:255] or None,
            )
        )
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            expires_in=self.settings.access_token_expire_minutes * 60,
        )

    async def _revoke_family(self, family_id: str, *, reason: str) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
        )

    async def _enforce_lockout(self, identifier: str, ip_address: str | None) -> None:
        window_start = datetime.now(UTC) - timedelta(
            minutes=self.settings.login_lockout_minutes
        )
        clauses = [LoginAttempt.identifier == identifier]
        if ip_address:
            clauses.append(LoginAttempt.ip_address == ip_address)

        failures = await self.session.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                or_(*clauses),
                LoginAttempt.successful.is_(False),
                LoginAttempt.attempted_at >= window_start,
            )
        )
        if (failures or 0) >= self.settings.login_max_attempts:
            raise AccountLockedError(
                "Too many failed attempts. Try again in "
                f"{self.settings.login_lockout_minutes} minutes.",
                details={"retry_after_minutes": self.settings.login_lockout_minutes},
            )

    async def _record_attempt(
        self,
        identifier: str,
        ip_address: str | None,
        successful: bool,
        user_agent: str | None,
    ) -> None:
        self.session.add(
            LoginAttempt(
                identifier=identifier,
                ip_address=ip_address,
                successful=successful,
                attempted_at=datetime.now(UTC),
                user_agent=user_agent,
            )
        )
        await self.session.flush()

    async def _touch_device(self, user: User, payload: LoginRequest) -> None:
        """Register the device, enforcing the plan's allowance.

        Raises DeviceLimitReachedError, which the login path lets through: the
        credentials were correct, so the user must be told what is wrong rather
        than shown a generic failure.
        """
        devices = DeviceService(self.session)
        if payload.replace_device:
            # A reinstall gets a new device id, so the same phone can find its
            # old self holding the only slot. Signing that one out from here
            # is what the device list offers anyway; the password has already
            # been verified by the time this runs.
            replaced = await devices.revoke(user.id, payload.replace_device)
            if replaced is None:
                raise NotFoundError("Device not found", code="DEVICE_NOT_FOUND")
        await devices.register(
            user.id,
            device_id=payload.device_id or "",
            device_name=payload.device_name,
            app_version=payload.app_version,
        )
