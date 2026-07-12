"""Async SQLAlchemy engine, session factory, and declarative base."""

import os
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Fusion merge (A6): ONE declarative Base / metadata across the whole app so
# cross-domain FKs resolve (e.g. cheesex agent_bindings.user_id -> main's user).
# cheesex models import Base from here; main's models import the same class from
# app.db.base_class — they are now the SAME registry.
from app.db.base_class import Base  # noqa: E402  (re-exported)

# Accept sync-style URLs (postgresql:// / +psycopg2) and normalize to asyncpg —
# some environments configure DATABASE_URL in the sync form (carried over from
# app.db.session, which used to build its own engine from the same setting).
_db_url = settings.database_url
if _db_url.startswith("postgresql://"):
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgresql+psycopg2://"):
    _db_url = _db_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)

# Under pytest one worker process runs MANY event loops (anyio function scope,
# blocking portals), and a pooled asyncpg connection must never hop loops — so
# the test conftest forces NullPool (fresh connection per checkout) via this
# env var BEFORE any app import. Production keeps the default QueuePool: one
# process, one loop, one pool.
_engine_kwargs: dict[str, Any] = (
    {"poolclass": NullPool} if os.environ.get("CHEESEX_TEST_NULLPOOL") else {}
)
engine = create_async_engine(_db_url, echo=settings.db_echo, **_engine_kwargs)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

__all__ = ["AsyncSession", "Base", "async_session_factory", "engine", "get_db"]


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
