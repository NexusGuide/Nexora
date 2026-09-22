"""Alembic environment.

The database URL comes from the application settings (i.e. the environment),
never from alembic.ini — so no migration config file can carry a password.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the models package registers every table on Base.metadata.
import app.models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Render ``EnumString`` columns as plain ``sa.String`` in migrations.

    At the database level ``EnumString`` is a VARCHAR; the enum coercion is
    application-side. Rendering it as ``sa.String`` keeps migrations free of an
    import on application code, so an old migration still runs after the enum
    has been moved or renamed.
    """
    from app.db.types import EnumString

    if type_ == "type" and isinstance(obj, EnumString):
        return f"sa.String(length={obj.impl.length})"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
