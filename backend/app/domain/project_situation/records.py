"""写「现状」用的记录：只取平台自己记下、点开就能核对的事。

- 项目目标：人写的那份文档；
- 近两周被采纳的交付：哪张卡、哪个任务、改动标题；
- 还开着的任务：任务状态机（``TaskStatus``）说它开着，和它的标题。

频道里人说的话、任务文档里 AI 队友写的进展都不取：那是有人说发生了什么，不是发生
了什么。一件事已经采纳了，就说「已采纳」；交付有没有上线，平台没有逐次的记录
（网站发布只记整棵树的版本，卡上也没有合并后的提交），所以不说。

每条记录在交给模型的材料里有一个短编号（D1、T1），模型写的每一行都要用编号说出
处；``card`` 拿编号回到这里核对。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.overview import pointed_overview
from app.domain.project.models import Project
from app.domain.project_material import public_rooms
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task, TaskStatus

WINDOW = timedelta(days=14)
#: 项目目标最多取多少字：比项目目标的写作预算（1500）宽，人写超了也看得到。
_GOAL_CHARS = 6000


@dataclass(frozen=True)
class Delivery:
    """一次被采纳的交付。"""

    ref: str
    card_id: uuid.UUID
    task_id: uuid.UUID
    room_id: uuid.UUID
    task_title: str
    #: 交付时写的改动标题，或交出去的文件名；都没有是空串。
    subject: str
    accepted_at: datetime


@dataclass(frozen=True)
class OpenTask:
    ref: str
    task_id: uuid.UUID
    room_id: uuid.UUID
    title: str


@dataclass(frozen=True)
class Records:
    project_name: str
    #: 项目目标写了什么（去掉首尾空白）；没写是空串。
    goal: str
    deliveries: list[Delivery]
    open_tasks: list[OpenTask]
    today: datetime

    @property
    def quiet(self) -> bool:
        """近两周没采纳过什么，也没有开着的任务：没东西可写。"""
        return not (self.deliveries or self.open_tasks)

    def by_ref(self) -> dict[str, Delivery | OpenTask]:
        return {r.ref: r for r in (*self.deliveries, *self.open_tasks)}

    def text(self) -> str:
        """交给模型的全文。"""
        goal = self.goal
        if len(goal) > _GOAL_CHARS:
            goal = goal[:_GOAL_CHARS] + "…"
        lines = [
            f"# 项目：{self.project_name}",
            f"今天：{self.today:%Y-%m-%d}",
            "",
            "## 项目目标（人写的文档）",
            goal or "（空）",
            "",
            "## 近两周已采纳的交付（新的在前）",
        ]
        lines += [
            f"- {d.ref}：任务「{d.task_title}」，{d.accepted_at:%m-%d} 采纳"
            + (f"，改动：{d.subject}" if d.subject else "")
            for d in self.deliveries
        ] or ["（没有）"]
        lines += ["", "## 还开着的任务"]
        lines += [f"- {t.ref}：「{t.title}」" for t in self.open_tasks] or ["（没有）"]
        return "\n".join(lines)


async def gather(
    session: AsyncSession, project_id: uuid.UUID, *, now: datetime | None = None
) -> Records:
    now = now or datetime.now(UTC)
    rooms = list((await public_rooms(session, project_id)).names)
    overview = await pointed_overview(session, project_id)
    name = await session.scalar(select(Project.name).where(Project.id == project_id))
    accepted = await session.execute(
        select(
            AcceptCard.id,
            Task.id,
            Task.room_id,
            Task.title,
            AcceptCard.change_subject,
            AcceptCard.deliverable_name,
            AcceptCard.decided_at,
        )
        .join(Task, Task.id == AcceptCard.task_id)
        .where(
            Task.project_id == project_id,
            Task.room_id.in_(rooms),
            AcceptCard.status == AcceptStatus.accepted,
            AcceptCard.decided_at > now - WINDOW,
        )
        .order_by(AcceptCard.decided_at.desc())
    )
    deliveries = [
        Delivery(
            ref=f"D{n}",
            card_id=card_id,
            task_id=task_id,
            room_id=room_id,
            task_title=title,
            subject=subject or deliverable or "",
            accepted_at=at,
        )
        for n, (
            card_id,
            task_id,
            room_id,
            title,
            subject,
            deliverable,
            at,
        ) in enumerate(accepted, start=1)
    ]
    going = await session.execute(
        select(Task.id, Task.room_id, Task.title)
        .where(
            Task.project_id == project_id,
            Task.room_id.in_(rooms),
            Task.status == TaskStatus.open,
        )
        .order_by(func.coalesce(Task.updated_at, Task.created_at).desc())
    )
    open_tasks = [
        OpenTask(ref=f"T{n}", task_id=task_id, room_id=room_id, title=title)
        for n, (task_id, room_id, title) in enumerate(going, start=1)
    ]
    return Records(
        project_name=name or "",
        goal=(overview.content if overview is not None else "").strip(),
        deliveries=deliveries,
        open_tasks=open_tasks,
        today=now,
    )


async def mark(session: AsyncSession, project_id: uuid.UUID) -> str:
    """项目此刻到了哪一步：任务开了、做完了、交付被采纳了、项目目标改了，它就变。

    不透明的字符串，只拿来和上次写的时候比相等不相等。
    """
    rooms = list((await public_rooms(session, project_id)).names)
    overview = await pointed_overview(session, project_id)
    tasks = (
        await session.execute(
            select(
                func.count(Task.id), func.max(Task.created_at), func.max(Task.closed_at)
            ).where(Task.project_id == project_id, Task.room_id.in_(rooms))
        )
    ).one()
    accepted = await session.scalar(
        select(func.max(AcceptCard.decided_at))
        .join(Task, Task.id == AcceptCard.task_id)
        .where(
            Task.project_id == project_id,
            Task.room_id.in_(rooms),
            AcceptCard.status == AcceptStatus.accepted,
        )
    )
    # The leading "records" sets this mark apart from the one the Markdown card
    # stored: a row the previous release rewrites during a deploy carries that
    # older mark, so it reads as due here instead of as an empty card in date.
    return "|".join(
        str(part)
        for part in (
            "records",
            overview.version if overview is not None else 0,
            *tasks,
            accepted,
        )
    )
