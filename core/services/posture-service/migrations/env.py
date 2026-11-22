from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from app.config import get_settings
from app.models import Base
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
VERSION_TABLE = "alembic_version_posture"


def _url(db_name: str) -> str:
    settings = get_settings()
    from libs.dbcore import build_database_url
    return build_database_url(
        settings.db_engine, settings.db_user, settings.db_password,
        settings.db_host, settings.db_port, db_name,
    )


async def _ensure_database() -> None:
    """Create seyalrun_posture if it does not exist yet.

    Fresh installs get it from core/docker-init, but that only runs on an EMPTY
    data volume — every existing deployment upgrading to 3.0 would otherwise have
    to know to create it by hand before this service could start. Best-effort:
    a DB user without CREATEDB gets a clear message instead of a stack trace.
    """
    settings = get_settings()
    name = settings.posture_db_name
    if settings.db_engine == "postgres":
        engine = create_async_engine(_url("postgres"), isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                exists = (await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).scalar()
                if not exists:
                    await conn.execute(text(f'CREATE DATABASE "{name}"'))
                    print(f"[posture] created database {name}", file=sys.stderr)
        except Exception as exc:
            print(f"[posture] could not ensure database {name} exists ({type(exc).__name__}: {exc}); "
                  f"create it manually (ops/init-db.sh) if the migration below fails", file=sys.stderr)
        finally:
            await engine.dispose()
    elif settings.db_engine == "mysql":
        engine = create_async_engine(_url(""), isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{name}`"))
        except Exception as exc:
            print(f"[posture] could not ensure database {name} exists ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
        finally:
            await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(get_settings().posture_db_name),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, version_table=VERSION_TABLE)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    await _ensure_database()
    connectable = create_async_engine(_url(get_settings().posture_db_name))
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
