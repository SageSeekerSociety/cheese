"""Memory routes — 记忆可见 (spec §8.4).

芝士 writes memories via the `remember` tool; humans must be able to SEE (and
prune) what it remembers, or the memory is a black box. Entries live in
``memory_entries``; ids are row UUIDs.

What an agent remembered in a project is that project's content, and what it
noted about a person is that person's business: the people who may read the
project read the one, the person reads the other, and nobody else reads either.

Deleting is safe curation, not data loss (memory is a projection).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.memory.models import (
    MemoryEntry,
    MemoryScope,
    agent_project_scope_id,
    project_of_scope,
    project_scope_prefix,
    user_scope_about,
    user_scope_id,
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
    resolver: ActorResolverDep,
    project_id: uuid.UUID,
    user_handle: str | None = None,
    agent_handle: str | None = None,
) -> dict:
    """Memory entries for a project and/or a person, newest first.

    Every memory here belongs to one agent instance (结论 8): `cheese_remember`
    lands in an ``agent_project`` pool keyed ``{project_id}:{handle}``, and
    ``project_id`` lists every such pool in the project; ``scope``/``scope_id``
    on each entry say which one it came from, and ``agent_handle`` narrows to
    one agent's. 项目自己没有池（结论 7）—— 全项目共看的那一份状态是总览房间的
    实况文档，由文档接口提供，不在这个列表里。

    ``user_handle`` asks the other question a person has about memory — what
    has been remembered *about me* — and it is answered INSIDE this project:
    a pool about a person belongs to one agent instance in one project (结论
    8), so listing it without a project would hand the reader another project's
    notes about the same person. It is asked by that person and nobody else:
    the agent itself reads its notes on someone only in that person's private
    chat with it, and a teammate has no better claim than the agent does.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    if user_handle and user_handle != actor.handle:
        raise ForbiddenError("只能查看关于你自己的记忆")
    agent_cond = MemoryEntry.scope == MemoryScope.agent_project
    if agent_handle:
        cond = agent_cond & (
            MemoryEntry.scope_id == agent_project_scope_id(project_id, agent_handle)
        )
    else:
        # Every agent that ever wrote here, including ones no longer on a
        # roster — a prefix scan is the only listing that can't go silently
        # blind. `project_id` is a parsed UUID, so it carries no LIKE
        # wildcards; autoescape guards the general case anyway.
        cond = agent_cond & MemoryEntry.scope_id.startswith(
            project_scope_prefix(project_id), autoescape=True
        )
    if user_handle:
        if agent_handle:
            about = MemoryEntry.scope_id == user_scope_id(
                project_id, agent_handle, user_handle
            )
        else:
            # 这个项目里每一位 agent 对他的记录。前缀锁住项目，后缀锁住
            # 人，中间那一段是谁记的——两头夹住才既不漏掉一位队友，也不
            # 把别的项目对同一个人的记录带进来。
            about = MemoryEntry.scope_id.startswith(
                project_scope_prefix(project_id), autoescape=True
            ) & MemoryEntry.scope_id.endswith(
                user_scope_about(user_handle), autoescape=True
            )
        cond = cond | ((MemoryEntry.scope == MemoryScope.user) & about)
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
async def delete_memory(
    entry_id: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """人工修剪一条记忆 (curation, not data loss — memory is a projection).

    An entry is pruned by someone the listing above would show it to: a pool
    of the project's agents by whoever may read the project, a note about a
    person by that person. Anyone else is told the entry does not exist,
    exactly as for an id that never did — a refusal that answered differently
    would confirm the id to someone with no business knowing it.
    """
    actor = await resolver.require_verified_caller()
    try:
        row_id = uuid.UUID(entry_id)
    except ValueError as exc:
        raise ValidationError("无效的记忆条目 id") from exc
    missing = NotFoundError("记忆条目不存在")
    entry = await db.get(MemoryEntry, row_id)
    if entry is None:
        raise missing
    project_id = project_of_scope(entry.scope, entry.scope_id)
    if project_id is None:
        raise missing
    try:
        await resolver.authorize_project(actor, project_id=project_id)
    except (ForbiddenError, NotFoundError) as exc:
        raise missing from exc
    if entry.scope is MemoryScope.user and not entry.scope_id.endswith(
        user_scope_about(actor.handle)
    ):
        raise missing
    await db.delete(entry)
    await db.flush()
    return ok({"deleted": entry_id})
