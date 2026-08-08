"""Dual-engine (PostgreSQL/MySQL) async SQLAlchemy setup.

A single ``DB_ENGINE`` choice (``postgres`` or ``mysql``) drives the whole
stack. Models use only generic SQLAlchemy types (String/JSON/DateTime/etc.)
so the same schema works unchanged on both engines.
"""

from __future__ import annotations

from typing import AsyncIterator
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def build_database_url(
    db_engine: str,
    user: str,
    password: str,
    host: str,
    port: str | int,
    name: str,
) -> str:
    """Build an async SQLAlchemy URL for ``postgres`` or ``mysql``."""
    user_q = quote_plus(user)
    pw_q = quote_plus(password)
    if db_engine == "mysql":
        return f"mysql+aiomysql://{user_q}:{pw_q}@{host}:{port}/{name}"
    if db_engine == "postgres":
        return f"postgresql+asyncpg://{user_q}:{pw_q}@{host}:{port}/{name}"
    raise ValueError(f"Unsupported DB_ENGINE: {db_engine!r} (expected 'postgres' or 'mysql')")


def _connect_args(db_engine: str, sslmode: str) -> dict:
    """Translate DB_SSLMODE into engine-specific connect_args."""
    if not sslmode or sslmode == "disable":
        return {}
    if db_engine == "postgres":
        # asyncpg: "disable" | "prefer" | "require" | "verify-ca" | "verify-full"
        return {"ssl": sslmode if sslmode != "require" else True}
    if db_engine == "mysql":
        # aiomysql: presence of an empty ssl dict enables TLS
        return {"ssl": {}}
    return {}


def make_engine(url: str, db_engine: str = "postgres", sslmode: str = "require") -> AsyncEngine:
    kwargs: dict = {"echo": False, "pool_pre_ping": True}
    connect_args = _connect_args(db_engine, sslmode)
    if connect_args:
        kwargs["connect_args"] = connect_args
    if db_engine == "postgres":
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
    return create_async_engine(url, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_async_session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
