"""What a space's manager sees of it, written out for their 芝士.

Before 芝士 changes a space for the person who runs it, it reads what is there:
the space's name, intro and how many problems each publisher may show, its
categories, how people join it, its announcements, and the problems waiting for
review — each with the id that acts on it. The names here also go into what
the gate reads (`api.assistant_acts`), so an action names the thing it
changes the way the person would.

Only someone who manages the space reads this; anyone else is told it is not
there.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.agent.personal.reading import Read
from app.domain.space import permissions as space_seats
from app.domain.space.models import (
    Space,
    SpaceAnnouncement,
    SpaceCategory,
)
from app.domain.task.models import Task
from app.domain.user.services import usernames_by_ids

#: A problem not yet reviewed (`task.inputs.APPROVE_NONE`).
_UNREVIEWED = 2
#: The most of each kind one read lists.
LISTED = 50


async def managed(session: AsyncSession, user_id: int, space_id: int) -> Space:
    """The space, when the person manages it; not found otherwise."""
    space = await session.get(Space, space_id)
    if space is None or not await space_seats.runs(session, space_id, user_id):
        raise NotFoundError.for_resource("space", str(space_id))
    return space


async def announcement_title(session: AsyncSession, space_id: int, id: int) -> str:
    row = await session.get(SpaceAnnouncement, id)
    if row is None or row.space_id != space_id:
        raise NotFoundError.for_resource("announcement", str(id))
    return row.title


async def category_name(session: AsyncSession, space_id: int, id: int) -> str:
    row = await session.get(SpaceCategory, id)
    if row is None or row.space_id != space_id:
        raise NotFoundError.for_resource("category", str(id))
    return row.name


async def problem(session: AsyncSession, space_id: int, id: int) -> tuple[str, str]:
    """A problem waiting for review on this space: its name, and who set it."""
    task = await session.get(Task, id)
    if task is None or task.space_id != space_id or task.deleted_at is not None:
        raise NotFoundError.for_resource("task", str(id))
    setter = (await usernames_by_ids(session, [task.creator_id])).get(task.creator_id)
    return task.name, setter or str(task.creator_id)


async def desk(session: AsyncSession, user_id: int, space_id: int) -> Read:
    space = await managed(session, user_id, space_id)
    limit = space.visible_task_limit
    lines = [
        f"空间「{space.name}」（spaces/{space_id}）",
        f"名称：{space.name}",
        f"简介：{space.intro or '（空）'}",
        "每位出题人可见的题目上限：" + ("不限制" if limit is None else str(limit)),
    ]
    categories = list(
        await session.scalars(
            select(SpaceCategory)
            .where(SpaceCategory.space_id == space_id)
            .where(SpaceCategory.deleted_at.is_(None))
            .order_by(SpaceCategory.display_order, SpaceCategory.id)
            .limit(LISTED)
        )
    )
    lines.append("分类：")
    lines += [
        f"- {c.name}（编号 {c.id}）" + ("，已归档" if c.archived_at else "")
        for c in categories
    ] or ["（无）"]
    lines.append(
        "加入方式：持加入链接的人"
        + ("申请，经管理员批准后加入" if space.join_approval else "直接加入")
        + "；链接在空间的成员管理页复制或重置"
    )
    announcements = list(
        await session.scalars(
            select(SpaceAnnouncement)
            .where(SpaceAnnouncement.space_id == space_id)
            .order_by(SpaceAnnouncement.id.desc())
            .limit(LISTED)
        )
    )
    lines.append("公告：")
    lines += [f"- 「{a.title}」（编号 {a.id}）" for a in announcements] or ["（无）"]
    waiting = list(
        await session.scalars(
            select(Task)
            .where(Task.space_id == space_id)
            .where(Task.approved == _UNREVIEWED)
            .where(Task.deleted_at.is_(None))
            .where(Task.ended_at.is_(None))
            .order_by(Task.created_at)
            .limit(LISTED)
        )
    )
    setters = await usernames_by_ids(session, {t.creator_id for t in waiting})
    lines.append("待审核的题目：")
    lines += [
        f"- 「{t.name}」（spaces/{space_id}/tasks/{t.id}），出题人 "
        + setters.get(t.creator_id, "?")
        for t in waiting
    ] or ["（无）"]
    return Read(f"空间「{space.name}」的设置", "\n".join(lines))
