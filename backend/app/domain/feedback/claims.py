"""领取：一条反馈有人在修，别人就别再修同一个问题。

领取就是把 `assignee_handle` 写成自己 —— 和管理员「指派」（`patch_admin`）写的是同一
列。不另开一列：两列会各说一个「谁在管它」，而领取本来就是「指派给我自己」。

这里只放三样 `FeedbackService.claim` / `release` 要的、仓储里没有的读：

* **按编号找**：人和 agent 手上拿的都是 `FB-12`（详情页印的、`Fixes-feedback:` 写的），
  不是 uuid，所以两样都认。
* **锁住再看**：`SELECT … FOR UPDATE`。两个同时到的领取，第二个在这里等第一个提交，
  然后读到它写下的持有人，于是失败 —— 「先读有没有人、再写」不锁的话两个都读到「没
  人」，后写的那个悄悄盖掉先到的，两个人都以为是自己在修。
* **谁在做知是本身**：反馈中心是平台的收件箱，能来修的是做这个平台的人。判据和开发
  文档同一个（`docs_site.library.platform_projects`，即 `settings.docs_dev_repositories`
  里那个仓库所在的项目）：agent 看它此刻所在房间的项目，人看他能不能进其中一个项目。
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_access import may_read_project
from app.domain.docs_site.library import platform_projects
from app.domain.feedback.models import Feedback

#: The timeline note on the 处理中 step a claim records.
CLAIMED_NOTE = "已领取"

_DISPLAY_REF = re.compile(r"^FB-(\d+)$", re.IGNORECASE)


async def find(session: AsyncSession, ref: str) -> Feedback | None:
    """The live row `ref` names — a uuid or a display id like `FB-12`."""
    ref = ref.strip()
    shown = _DISPLAY_REF.match(ref)
    if shown:
        where = Feedback.display_no == int(shown.group(1))
    else:
        try:
            where = Feedback.id == uuid.UUID(ref)
        except ValueError:
            return None
    return await session.scalar(
        select(Feedback).where(where, Feedback.deleted_at.is_(None))
    )


async def lock(session: AsyncSession, feedback_id: uuid.UUID) -> Feedback | None:
    """The row, locked for the rest of the caller's transaction and re-read.

    `populate_existing` matters: the row is usually already in the session (the
    visibility check loaded it), and without it the ORM hands back that copy —
    read before the lock, so blind to the claim that just committed.
    """
    return await session.scalar(
        select(Feedback)
        .where(Feedback.id == feedback_id, Feedback.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def works_on_platform(
    session: AsyncSession, handle: str, *, room_project_id: uuid.UUID | None
) -> bool:
    """Whether `handle` is someone working on the platform itself.

    `room_project_id` is the project of the room the request was made from, once
    the route has authorized the caller in it — how an agent answers: its
    credential is bound to a room, and the room's project is what it works on.
    A person asking from the feedback center names no room, and qualifies by
    being able to enter one of the platform's projects.
    """
    projects = await platform_projects(session)
    if room_project_id is not None and room_project_id in projects:
        return True
    for project_id in projects:
        if await may_read_project(session, project_id=project_id, handle=handle):
            return True
    return False
