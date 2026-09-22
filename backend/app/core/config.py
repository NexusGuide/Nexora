"""Application settings.

Every secret is read from the environment. Nothing in this module carries a
usable default: if a secret is missing, too short, still set to a placeholder,
or reused where it must be unique, the application refuses to start rather
than running with a weak value.

That refusal is deliberate. A silent fallback default is how a development
secret ends up signing production tokens.
"""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that mean "not configured yet". Matching any of these is an error,
# so a copied .env.example can never boot.
_PLACEHOLDER_RE = re.compile(
    r"^\s*(<[^>]*>|changeme|change_me|placeholder|your[-_ ]?\w+|example|dummy|"
    r"secret|test|todo|xxx+)\s*$",
    re.IGNORECASE,
)

MIN_SECRET_LENGTH = 32

Environment = Literal["development", "staging", "production"]


def _looks_like_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value))


class Settings(BaseSettings):
    """Runtime configuration, validated at import time."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_env: Environment = "development"
    app_name: str = "NexusVPN"
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    public_api_url: str = "http://localhost:8000"

    cors_origins: str = "http://localhost:3000"
    allowed_hosts: str = "localhost,127.0.0.1"

    # --- Database / cache --------------------------------------------------
    database_url: str = Field(..., description="Async SQLAlchemy URL")
    redis_url: str = "redis://redis:6379/0"

    # --- Tokens ------------------------------------------------------------
    jwt_secret: str = Field(..., min_length=MIN_SECRET_LENGTH)
    jwt_refresh_secret: str = Field(..., min_length=MIN_SECRET_LENGTH)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # --- Data at rest ------------------------------------------------------
    encryption_key: str = Field(..., description="urlsafe-base64, 32 bytes")

    # --- Admin bootstrap ---------------------------------------------------
    admin_bootstrap_secret: str = Field(..., min_length=MIN_SECRET_LENGTH)

    # --- Rate limiting -----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15

    # --- Payments ----------------------------------------------------------
    payment_provider: str = "manual"
    payment_api_key: str = ""
    payment_callback_url: str = ""

    # --- Optional integrations --------------------------------------------
    pasarguard_url: str = ""
    pasarguard_username: str = ""
    pasarguard_password: str = ""
    fcm_project_id: str = ""
    fcm_credentials_file: str = ""
    sentry_dsn: str = ""
    metrics_enabled: bool = False

    # ----------------------------------------------------------------- utils
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    # ------------------------------------------------------------ validators
    @field_validator("jwt_secret", "jwt_refresh_secret", "admin_bootstrap_secret")
    @classmethod
    def _reject_placeholder_secrets(cls, v: str, info: ValidationInfo) -> str:
        if _looks_like_placeholder(v):
            raise ValueError(
                f"{info.field_name.upper()} is still a placeholder. "
                "Generate a real value: openssl rand -hex 32"
            )
        if len(set(v)) < 8:
            raise ValueError(
                f"{info.field_name.upper()} has too little entropy "
                "(fewer than 8 distinct characters)."
            )
        return v

    @field_validator("encryption_key")
    @classmethod
    def _validate_encryption_key(cls, v: str) -> str:
        if _looks_like_placeholder(v):
            raise ValueError(
                "ENCRYPTION_KEY is still a placeholder. Generate one with: "
                'python -c "import base64,os;'
                'print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
            )
        try:
            raw = base64.urlsafe_b64decode(v.encode())
        except Exception as exc:
            raise ValueError("ENCRYPTION_KEY must be urlsafe-base64 encoded.") from exc
        if len(raw) != 32:
            raise ValueError(
                f"ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(raw)}."
            )
        return v

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        if _looks_like_placeholder(v):
            raise ValueError("DATABASE_URL is still a placeholder.")
        if "<" in v and ">" in v:
            raise ValueError("DATABASE_URL still contains an angle-bracket placeholder.")
        if not v.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
            raise ValueError(
                "DATABASE_URL must use an async driver "
                "(postgresql+asyncpg:// or sqlite+aiosqlite:// for tests)."
            )
        return v

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Settings:
        if self.jwt_secret == self.jwt_refresh_secret:
            raise ValueError(
                "JWT_SECRET and JWT_REFRESH_SECRET must be different values. "
                "Sharing them lets a refresh token be replayed as an access token."
            )
        if self.admin_bootstrap_secret in {self.jwt_secret, self.jwt_refresh_secret}:
            raise ValueError("ADMIN_BOOTSTRAP_SECRET must not reuse a JWT secret.")

        if self.is_production:
            problems: list[str] = []
            if self.app_debug:
                problems.append("APP_DEBUG must be false in production.")
            if "*" in self.cors_origin_list:
                problems.append("CORS_ORIGINS must not be '*' in production.")
            if "*" in self.allowed_host_list:
                problems.append("ALLOWED_HOSTS must not be '*' in production.")
            if self.public_api_url.startswith("http://"):
                problems.append("PUBLIC_API_URL must use HTTPS in production.")
            if "sqlite" in self.database_url:
                problems.append("SQLite is not supported in production.")
            if problems:
                raise ValueError(
                    "Production configuration rejected:\n  - " + "\n  - ".join(problems)
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — the single source of configuration."""
    return Settings()  # type: ignore[call-arg]
