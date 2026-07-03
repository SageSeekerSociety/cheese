"""Memory routes — 记忆可见 (spec §8.4).

芝士 writes memories via the `remember` tool; humans must be able to SEE (and
prune) what it remembers, or the memory is a black box. Entries are a
projection rebuildable from blocks, so deleting one is safe curation, not data
loss.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.domain.memory.models import MemoryEntry, MemoryScope

router = APIRouter(prefix="/api/memory", tags=["memory"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _entry_out(e: MemoryEntry) -> dict:
    return {
        "id": str(e.id),
        "scope": e.scope.value,
        "scope_id": e.scope_id,
        "content": e.content,
        "created_at": e.created_at.isoformat(),
    }


@router.get("")
async def list_memory(
    db: DbSession,
    project_id: uuid.UUID | None = None,
    user_handle: str | None = None,
) -> dict:
    """Memory entries for a project and/or a user, newest first."""
    conds = []
    if project_id is not None:
        conds.append(
            (MemoryEntry.scope == MemoryScope.project)
            & (MemoryEntry.scope_id == str(project_id))
        )
    if user_handle:
        conds.append(
            (MemoryEntry.scope == MemoryScope.user)
            & (MemoryEntry.scope_id == user_handle)
        )
    if not conds:
        return ok(page([], 0))
    cond = conds[0]
    for c in conds[1:]:
        cond = cond | c
    rows = (
        await db.scalars(
            select(MemoryEntry).where(cond).order_by(MemoryEntry.created_at.desc())
        )
    ).all()
    return ok(page([_entry_out(e) for e in rows], len(rows)))


@router.delete("/{entry_id}")
async def delete_memory(entry_id: uuid.UUID, db: DbSession) -> dict:
    """人工修剪一条记忆 (curation, not data loss — memory is a projection)."""
    entry = await db.get(MemoryEntry, entry_id)
    if entry is None:
        raise NotFoundError("记忆条目不存在")
    await db.delete(entry)
    await db.flush()
    return ok({"deleted": str(entry_id)})
