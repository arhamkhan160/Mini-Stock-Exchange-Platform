"""Async SQLAlchemy 2.0 engine/session helpers.

Contract:
  * App uses `postgresql+asyncpg://...`  (DATABASE_URL)
  * Alembic uses `postgresql+psycopg2://...` — call `sync_url()` in env.py.
    Alembic cannot drive asyncpg with the default template; this is the #1 trap.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base. Every service defines its own tables on this base."""


def make_engine(url: str, **kwargs) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_pre_ping=True,   # survives Postgres restarts during development
        pool_size=10,
        max_overflow=20,
        future=True,
        **kwargs,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def sync_url(url: str) -> str:
    """asyncpg URL -> psycopg2 URL, for Alembic."""
    return url.replace("+asyncpg", "+psycopg2")


def session_dependency(session_factory: async_sessionmaker[AsyncSession]):
    """Build a FastAPI dependency that yields a session and always closes it."""

    async def _dep() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    return _dep
