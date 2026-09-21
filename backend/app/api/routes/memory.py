"""Memory routes — 记忆可见 (spec §8.4).

芝士 writes memories via the `remember` tool; humans must be able to SEE (and
prune) what it remembers, or the memory is a black box. Entries live in
``memory_entries``; ids are row UUIDs.

Deleting is safe curation, not data loss (memory is a projection).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationError
from app.domain.memory.models import (
    MemoryEntry,
    MemoryScope,
    agent_project_scope_id,
    agent_project_scope_prefix,
)
from app.domain.memory.store import live_entries

router = APIRouter(prefix="/memory", tags=["memory"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _entry_out(e: MemoryEntry) -> dict:
    return {
        "id": str(e.id),
        "scope": e.scope.value,
        "scope_id": e.scope_id,
        "content": e.content,
        "layer": e.layer.value,
        "created_at": e.created_at.isoformat(),
        # 记忆整理 computes its snapshot from the newest of these, so that the
        # concurrency check compares two timestamps from the SAME clock — the
        # sandbox's own "now" is a different one, and a container running fast
        # would quietly stop protecting concurrent writes.
        "updated_at": e.updated_at.isoformat(),
    }


@router.get("")
async def list_memory(
    db: DbSession,
    project_id: uuid.UUID | None = None,
    user_handle: str | None = None,
    agent_handle: str | None = None,
    include_agent: bool = True,
) -> dict:
    """Memory entries for a project and/or a user, newest first.

    A project's memory includes what its 芝士 remembered: `cheese remember`
    always carries a topic, so in practice every agent write lands in an
    ``agent_project`` pool keyed ``{project_id}:{handle}``. Leaving those out
    made this endpoint blind to the whole live pool, so ``project_id`` sweeps
    them in by default; ``scope``/``scope_id`` on each entry say where it came
    from. ``agent_handle`` narrows to one agent's pool, ``include_agent=false``
    is the escape hatch back to the shared project pool alone (it wins over
    ``agent_handle`` if both are given).
    """
    conds = []
    if project_id is not None:
        conds.append(
            (MemoryEntry.scope == MemoryScope.project)
            & (MemoryEntry.scope_id == str(project_id))
        )
        if include_agent:
            agent_cond = MemoryEntry.scope == MemoryScope.agent_project
            if agent_handle:
                conds.append(
                    agent_cond
                    & (
                        MemoryEntry.scope_id
                        == agent_project_scope_id(project_id, agent_handle)
                    )
                )
            else:
                # Every agent that ever wrote here, including ones no longer on
                # a roster — a prefix scan is the only listing that can't go
                # silently blind. `project_id` is a parsed UUID, so it carries
                # no LIKE wildcards; autoescape guards the general case anyway.
                conds.append(
                    agent_cond
                    & MemoryEntry.scope_id.startswith(
                        agent_project_scope_prefix(project_id), autoescape=True
                    )
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
    # Same filter the recall path uses: a fact 记忆整理 retired is no longer part
    # of the memory, and showing it here would tell a human the opposite of what
    # 芝士 will actually read next turn.
    rows = (
        await db.scalars(
            select(MemoryEntry)
            .where(cond, live_entries())
            .order_by(MemoryEntry.created_at.desc())
        )
    ).all()
    return ok(page([_entry_out(e) for e in rows], len(rows)))


@router.delete("/{entry_id}")
async def delete_memory(entry_id: str, db: DbSession) -> dict:
    """人工修剪一条记忆 (curation, not data loss — memory is a projection)."""
    try:
        row_id = uuid.UUID(entry_id)
    except ValueError as exc:
        raise ValidationError("无效的记忆条目 id") from exc
    entry = await db.get(MemoryEntry, row_id)
    if entry is None:
        raise NotFoundError("记忆条目不存在")
    await db.delete(entry)
    await db.flush()
    return ok({"deleted": entry_id})
