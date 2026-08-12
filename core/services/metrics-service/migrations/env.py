"""No-op Alembic env for metrics-service (read-only, no own schema).

This exists only to stamp alembic_version_metrics so verify-staging.sh's
uniform 'alembic current==head' check passes.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


class _EmptyBase(DeclarativeBase):
    pass


target_metadata = _EmptyBase.metadata


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version_metrics",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    settings = get_settings()
    from libs.dbcore import build_database_url
    url = build_database_url(
        settings.db_engine, settings.db_user, settings.db_password,
        settings.db_host, settings.db_port, settings.automation_db_name,
    )
    connectable = create_async_engine(url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    pass
else:
    run_migrations_online()
