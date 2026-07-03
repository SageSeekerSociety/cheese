"""Memory store abstraction.

Phase 0 ships a DB-backed projection (DbMemoryStore). The Protocol lets us swap
in OpenViking (`viking://` paths, spec §8.4) later without changing callers.
The block tree remains the source of truth; this is a fast-recall projection.
"""

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory.models import MemoryEntry, MemoryScope


class MemoryStore(Protocol):
    async def recall(
        self, scope: MemoryScope, scope_id: str, limit: int = 50
    ) -> list[str]:
        """Return up to `limit` MOST RECENT memory facts, oldest first.
        The cap keeps prompt injection bounded until real layered memory
        (OpenViking, spec §15 Q9) replaces flat full recall."""
        ...

    async def remember(self, scope: MemoryScope, scope_id: str, content: str) -> None:
        """Persist a new memory fact."""
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
