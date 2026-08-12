from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    settings = get_settings()
    from libs.dbcore import build_database_url
    url = build_database_url(
        settings.db_engine, settings.db_user, settings.db_password,
        settings.db_host, settings.db_port, settings.automation_db_name,
    )
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        version_table="alembic_version_zabbix_integration",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection, target_metadata=target_metadata,
        version_table="alembic_version_zabbix_integration",
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
    run_migrations_offline()
else:
    run_migrations_online()
