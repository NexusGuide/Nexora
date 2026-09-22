"""Test fixtures.

Every secret here is a deterministic, clearly-labelled test value suffixed
``-for-tests-only``. The gitleaks allowlist recognises that suffix, and no
value in this file is usable anywhere outside the test suite.
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncGenerator

import pytest

# Environment must be set before app.core.config is imported anywhere.
TEST_ENV = {
    "APP_ENV": "development",
    "APP_DEBUG": "true",
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "REDIS_URL": "redis://localhost:6379/15",
    "JWT_SECRET": "a1b2c3d4e5f60718293a4b5c6d7e8f90-access-for-tests-only",
    "JWT_REFRESH_SECRET": "f0e9d8c7b6a5948372615f4e3d2c1b0a-refresh-for-tests-only",
    "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"n" * 32).decode(),
    "ADMIN_BOOTSTRAP_SECRET": "9f8e7d6c5b4a39281706f5e4d3c2b1a0-admin-for-tests-only",
    "LOGIN_MAX_ATTEMPTS": "5",
    "LOGIN_LOCKOUT_MINUTES": "15",
}
os.environ.update(TEST_ENV)

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: E402,F401  (registers tables on Base.metadata)
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """A fresh in-memory database per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
