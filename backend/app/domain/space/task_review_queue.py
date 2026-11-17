"""What waits on the platform's admins: 题目 not yet reviewed, in every space.

A space's own queue is its reviewers' (``GET /tasks?approved=NONE``); this one
is ``/admin/tasks``, read in the same order. Only a space that still exists
counts: deleting a space leaves its 题目 rows, and nobody can open what they
belong to.
"""

from __future__ import annotations

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import Space
from app.domain.task.inputs import APPROVE_NONE
from app.domain.task.models import Task

_WAITING = (
    Task.deleted_at.is_(None),
    Task.approved == APPROVE_NONE,
    exists().where(Space.id == Task.space_id, Space.deleted_at.is_(None)),
)


async def unreviewed_tasks(
    session: AsyncSession, *, limit: int, offset: int = 0
) -> list[Task]:
    """Oldest first, as a space's own queue reads."""
    stmt = (
        select(Task)
        .where(*_WAITING)
        .order_by(Task.created_at.asc(), Task.id.asc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.scalars(stmt)).all())


async def count_unreviewed(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(Task.id)).where(*_WAITING)) or 0)
