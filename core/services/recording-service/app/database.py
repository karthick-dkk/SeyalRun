from __future__ import annotations

from libs.dbcore import Base, build_database_url, get_async_session, make_engine, make_sessionmaker
from .config import get_settings

settings = get_settings()

_url = build_database_url(
    settings.db_engine,
    settings.db_user,
    settings.db_password,
    settings.db_host,
    settings.db_port,
    settings.terminal_db_name,
)

engine = make_engine(_url, settings.db_engine, settings.db_sslmode)
SessionLocal = make_sessionmaker(engine)


async def get_session():
    async for session in get_async_session(SessionLocal):
        yield session


__all__ = ["Base", "engine", "SessionLocal", "get_session"]
