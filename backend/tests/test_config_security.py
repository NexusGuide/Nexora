"""The configuration layer must refuse to boot on a weak or placeholder secret.

These are the tests that keep the "no secrets in the repo" guarantee from
degrading into "a default secret shipped in the repo".
"""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings

GOOD_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
BASE = {
    "database_url": "sqlite+aiosqlite:///:memory:",
    "jwt_secret": "1a2b3c4d5e6f708192a3b4c5d6e7f809-access-for-tests-only",
    "jwt_refresh_secret": "9z8y7x6w5v4u3t2s1r0q9p8o7n6m5l4k-refresh-for-tests-only",
    "encryption_key": GOOD_KEY,
    "admin_bootstrap_secret": "0q1w2e3r4t5y6u7i8o9p0a1s2d3f4g5h-admin-for-tests-only",
}


def make(**overrides) -> Settings:
    return Settings(**{**BASE, **overrides})  # type: ignore[arg-type]


def test_valid_configuration_loads():
    settings = make()
    assert settings.app_env == "development"
    assert settings.jwt_secret != settings.jwt_refresh_secret


@pytest.mark.parametrize(
    "placeholder",
    [
        "<generate-with-openssl-rand-hex-32>",
        "changeme",
        "your-secret-here",
        "placeholder",
    ],
)
def test_placeholder_jwt_secret_is_rejected(placeholder):
    with pytest.raises(ValidationError):
        make(jwt_secret=placeholder)


def test_short_jwt_secret_is_rejected():
    with pytest.raises(ValidationError):
        make(jwt_secret="tooshort")


def test_low_entropy_secret_is_rejected():
    with pytest.raises(ValidationError):
        make(jwt_secret="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")


def test_reused_jwt_secrets_are_rejected():
    """Sharing the secret would let a refresh token be replayed as access."""
    with pytest.raises(ValidationError) as exc:
        make(jwt_refresh_secret=BASE["jwt_secret"])
    assert "different" in str(exc.value).lower()


def test_admin_secret_may_not_reuse_a_jwt_secret():
    with pytest.raises(ValidationError):
        make(admin_bootstrap_secret=BASE["jwt_secret"])


@pytest.mark.parametrize(
    "bad_key",
    [
        "<generate-32-byte-urlsafe-base64-key>",
        "not-base64!!!",
        base64.urlsafe_b64encode(b"tooshort").decode(),
    ],
)
def test_bad_encryption_key_is_rejected(bad_key):
    with pytest.raises(ValidationError):
        make(encryption_key=bad_key)


def test_database_url_placeholder_is_rejected():
    with pytest.raises(ValidationError):
        make(database_url="postgresql+asyncpg://nexus:<db-password>@db:5432/nexus")


def test_sync_database_driver_is_rejected():
    with pytest.raises(ValidationError):
        make(database_url="postgresql://nexus:pw@db:5432/nexus")


# --- production hardening ---------------------------------------------------


def test_production_rejects_debug():
    with pytest.raises(ValidationError) as exc:
        make(
            app_env="production",
            app_debug=True,
            database_url="postgresql+asyncpg://u:p@db:5432/n",
            public_api_url="https://api.example.com",
        )
    assert "APP_DEBUG" in str(exc.value)


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValidationError) as exc:
        make(
            app_env="production",
            cors_origins="*",
            database_url="postgresql+asyncpg://u:p@db:5432/n",
            public_api_url="https://api.example.com",
        )
    assert "CORS_ORIGINS" in str(exc.value)


def test_production_rejects_plain_http_public_url():
    with pytest.raises(ValidationError) as exc:
        make(
            app_env="production",
            database_url="postgresql+asyncpg://u:p@db:5432/n",
            public_api_url="http://api.example.com",
        )
    assert "HTTPS" in str(exc.value)


def test_production_rejects_sqlite():
    with pytest.raises(ValidationError):
        make(app_env="production", public_api_url="https://api.example.com")


def test_production_accepts_a_hardened_configuration():
    settings = make(
        app_env="production",
        app_debug=False,
        database_url="postgresql+asyncpg://u:p@db:5432/n",
        public_api_url="https://api.example.com",
        cors_origins="https://admin.example.com",
        allowed_hosts="api.example.com",
    )
    assert settings.is_production
    assert settings.cors_origin_list == ["https://admin.example.com"]
