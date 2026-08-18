"""Memory routes — 记忆可见 (spec §8.4).

芝士 writes memories via the `remember` tool; humans must be able to SEE (and
prune) what it remembers, or the memory is a black box. Two backends:

- db: entries live in memory_entries; ids are row UUIDs.
- openviking: entries are memory files in the scope's viking:// tree; the
  exposed id is a URL-safe base64 of the viking:// URI (opaque to clients,
  reversible here), content is the file's L0 abstract.

Deleting is safe curation, not data loss (memory is a projection).
"""

import base64
import binascii
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.core.config import settings
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
        "created_at": e.created_at.isoformat(),
        # 记忆整理 computes its snapshot from the newest of these, so that the
        # concurrency check compares two timestamps from the SAME clock — the
        # sandbox's own "now" is a different one, and a container running fast
        # would quietly stop protecting concurrent writes.
        "updated_at": e.updated_at.isoformat(),
    }


def _encode_uri(uri: str) -> str:
    return base64.urlsafe_b64encode(uri.encode()).decode().rstrip("=")


def _decode_uri(entry_id: str) -> str:
    pad = "=" * (-len(entry_id) % 4)
    try:
        uri = base64.urlsafe_b64decode(entry_id + pad).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationError("无效的记忆条目 id") from exc
    if not uri.startswith("viking://user/"):
        raise ValidationError("无效的记忆条目 id")
    return uri


async def _list_openviking(
    project_id: uuid.UUID | None,
    user_handle: str | None,
    agent_handle: str | None,
) -> list[dict]:
    """OpenViking listing.

    Known gap vs the db backend: a named ``agent_handle`` is listable (its pool
    is one more scope space), but "every agent pool in this project" is NOT.
    Each scope gets an isolated ``viking://user/{uid}`` tree and the store has
    no cross-space enumeration primitive, so the handle set would have to be
    guessed from the current rosters — which loses exactly the pools this
    endpoint exists to surface (an agent that wrote memory and later left the
    roster). A wrong list is worse than a documented gap, so the sweep is left
    to the db backend and this is stated rather than silently half-done.
    """
    from app.domain.memory.openviking_store import OpenVikingMemoryStore

    store = OpenVikingMemoryStore()
    wanted: list[tuple[MemoryScope, str]] = []
    if project_id is not None:
        wanted.append((MemoryScope.project, str(project_id)))
        if agent_handle:
            wanted.append(
                (
                    MemoryScope.agent_project,
                    agent_project_scope_id(project_id, agent_handle),
                )
            )
    if user_handle:
        wanted.append((MemoryScope.user, user_handle))
    items: list[dict] = []
    for scope, scope_id in wanted:
        for e in await store.list_entries(scope, scope_id):
            items.append(
                {
                    "id": _encode_uri(e["uri"]),
                    "scope": scope.value,
                    "scope_id": scope_id,
                    "content": f"[{e['rel_path']}] {e['abstract']}".strip(),
                    "created_at": e["mod_time"],
                }
            )
    items.sort(key=lambda d: d["created_at"], reverse=True)
    return items


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
    if settings.memory_backend == "openviking":
        items = await _list_openviking(
            project_id, user_handle, agent_handle if include_agent else None
        )
        return ok(page(items, len(items)))

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
    if settings.memory_backend == "openviking":
        from app.domain.memory.openviking_store import forget_uri

        uri = _decode_uri(entry_id)
        await forget_uri(uri)
        return ok({"deleted": entry_id})

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
