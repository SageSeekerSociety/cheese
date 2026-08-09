"""Memory store abstraction.

Two backends behind one Protocol (settings.memory_backend):
- DbMemoryStore  — flat memory_entries projection in PG (Phase 0 default).
- OpenVikingMemoryStore — embedded OpenViking layered memory (viking:// FS,
  L0 abstracts injected, full text behind semantic search; spec §8.4/§15 Q9).

The block tree remains the source of truth; memory is a fast-recall projection.
"""

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.memory.models import MemoryEntry, MemoryScope


class MemoryStore(Protocol):
    async def recall(
        self, scope: MemoryScope, scope_id: str, limit: int = 50
    ) -> list[str]:
        """Return up to `limit` memory facts for prompt injection, oldest first.
        DB backend: most recent raw facts. OpenViking backend: L0 abstract
        lines (summaries only — the layered-memory contract)."""
        ...

    async def remember(self, scope: MemoryScope, scope_id: str, content: str) -> None:
        """Persist a new memory fact."""
        ...

    async def search(
        self, scope: MemoryScope, scope_id: str, query: str, limit: int = 8
    ) -> list[Any]:
        """Query memories on demand (芝士's recall tool). Backends return
        objects with an ``as_dict()`` method."""
        ...


class DbMemoryStore:
    """MemoryStore backed by the memory_entries table."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def recall(
        self, scope: MemoryScope, scope_id: str, limit: int = 50
    ) -> list[str]:
        stmt = (
            select(MemoryEntry)
            .where(
                MemoryEntry.scope == scope,
                MemoryEntry.scope_id == scope_id,
            )
            .order_by(MemoryEntry.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [r.content for r in reversed(rows)]

    async def remember(self, scope: MemoryScope, scope_id: str, content: str) -> None:
        self._session.add(MemoryEntry(scope=scope, scope_id=scope_id, content=content))
        await self._session.flush()

    async def search(
        self, scope: MemoryScope, scope_id: str, query: str, limit: int = 8
    ) -> list[Any]:
        """Substring match — the flat backend has no semantic index. This is a
        structural filter over stored facts, not NL interpretation (规则4)."""
        from app.domain.memory.openviking_store import MemoryHit

        stmt = (
            select(MemoryEntry)
            .where(
                MemoryEntry.scope == scope,
                MemoryEntry.scope_id == scope_id,
                MemoryEntry.content.ilike(f"%{query}%"),
            )
            .order_by(MemoryEntry.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [
            MemoryHit(uri=f"db://memory/{r.id}", abstract=r.content, score=1.0)
            for r in rows
        ]


def memory_store(session: AsyncSession) -> MemoryStore:
    """Backend selector (settings.memory_backend: "db" | "openviking").

    The OpenViking store is process-global and ignores the DB session; the
    parameter keeps one call shape for both backends."""
    if settings.memory_backend == "openviking":
        # Lazy import: the openviking package pulls heavy deps (litellm etc.)
        # that "db" deployments never need to load.
        from app.domain.memory.openviking_store import OpenVikingMemoryStore

        return OpenVikingMemoryStore()
    return DbMemoryStore(session)
