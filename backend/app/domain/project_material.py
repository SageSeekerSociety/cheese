"""写「现状」时交给模型的那份材料，以及判断「上次之后有没有新进展」的两档记号。

材料按 2026-10-13 定的那版取：项目目标（总览文档）、近两周做完的事（开工时的标题和
交付时的改动标题）、还在做的事（标题和任务文档开头）、每个频道近两周人说的最后 20
条，都按频道分组。只取公开频道：私聊和私密频道里的事，项目里不是人人看得到，而现状
是人人看得到的。

起草项目总览（文档芝士的「起草」）读的也是这一份，所以它单独在这一层：写现状的
``project_situation`` 在最上层，文档芝士在 ``agent`` 那一层，两边都往下读它。
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.block.models import Block, BlockKind
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.library import records as library_records
from app.domain.library.shelf import Shelf
from app.domain.living_doc.models import Document
from app.domain.living_doc.overview import pointed_overview
from app.domain.project.models import Project
from app.domain.project_material_rows import (
    STATUS_ACCEPTED,
    STATUS_OPEN,
    _cards,
    _tasks,
)
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic, TopicKind, TopicStatus, not_a_private_chat
from app.domain.user.models import User, UserProfile

WINDOW = timedelta(days=14)
#: 每个频道交给模型的人话条数。
TALK_PER_ROOM = 20
#: 一条话、一份任务文档开头最多取多少字。
_LINE_CHARS = 200
_DOC_HEAD_CHARS = 260
#: 项目目标最多取多少字：比项目目标的写作预算（1500）宽，人写超了也看得到。
_GOAL_CHARS = 6000
#: 一次最多读回多少条人话；按频道分好以后每个频道只留最后 20 条。
_TALK_ROWS = 3000


@dataclass(frozen=True)
class TaskRow:
    """`tasks` 的一行，只带上本模块读的那几列。"""

    id: uuid.UUID
    room_id: uuid.UUID
    title: str
    document_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None
    open: bool


async def _task_rows(
    session: AsyncSession, project_id: uuid.UUID, room_ids: list[uuid.UUID]
) -> list[TaskRow]:
    """这些频道里的活，带上本模块要的那几列（`_tasks` 是裸表副本）。"""
    if not room_ids:
        return []
    rows = await session.execute(
        select(
            _tasks.c.id,
            _tasks.c.room_id,
            _tasks.c.title,
            _tasks.c.document_id,
            _tasks.c.created_at,
            _tasks.c.updated_at,
            _tasks.c.status,
        ).where(_tasks.c.project_id == project_id, _tasks.c.room_id.in_(room_ids))
    )
    return [
        TaskRow(
            id=row.id,
            room_id=row.room_id,
            title=row.title,
            document_id=row.document_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            open=row.status == STATUS_OPEN,
        )
        for row in rows
    ]


def _cut(text: str | None, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + "…"


@dataclass(frozen=True)
class Rooms:
    """项目里人人看得到的频道：id 到频道名。"""

    names: dict[uuid.UUID, str]


async def public_rooms(session: AsyncSession, project_id: uuid.UUID) -> Rooms:
    if isinstance(session, AsyncSession):
        if session.autoflush:
            await session.flush()
        found, cached = recall(session, ("public_rooms", project_id))
        if found:
            return cached
    rows = await session.execute(
        select(Topic.id, Topic.title).where(
            Topic.project_id == project_id,
            Topic.kind.in_((TopicKind.root, TopicKind.topic)),
            Topic.status == TopicStatus.active,
            Topic.deleted_at.is_(None),
            not_a_private_chat(),
            Topic.members_only.is_(False),
        )
    )
    out = Rooms(dict(rows.tuples().all()))
    if isinstance(session, AsyncSession):
        remember(
            session,
            ("public_rooms", project_id),
            out,
            tables=frozenset({"topics"}),
        )
    return out


@dataclass(frozen=True)
class Marks:
    """项目此刻到了哪一步，分两档。

    ``major``：任务开了、做完了、交付被采纳了、项目目标改了——看现状的人会在意的
    变化，几分钟后就该写进去。``minor``：频道里有人说了话——要写进去，但不急。
    两者都是不透明的字符串，只拿来和上次写的时候比相等不相等。
    """

    major: str
    minor: str


async def marks(session: AsyncSession, project_id: uuid.UUID) -> Marks:
    rooms = await public_rooms(session, project_id)
    room_ids = list(rooms.names)
    overview = await pointed_overview(session, project_id)
    task_rows = (
        list(
            (
                await session.execute(
                    select(
                        _tasks.c.id,
                        _tasks.c.created_at,
                        _tasks.c.closed_at,
                    ).where(
                        _tasks.c.project_id == project_id,
                        _tasks.c.room_id.in_(room_ids),
                    )
                )
            ).all()
        )
        if room_ids
        else []
    )
    task_ids = [r[0] for r in task_rows]
    tasks = (
        len(task_rows),
        max((r[1] for r in task_rows if r[1] is not None), default=None),
        max((r[2] for r in task_rows if r[2] is not None), default=None),
    )
    accepted = (
        await session.scalar(
            select(func.max(_cards.c.decided_at)).where(
                _cards.c.task_id.in_(task_ids),
                _cards.c.status == STATUS_ACCEPTED,
            )
        )
        if task_ids
        else None
    )
    said = await session.scalar(
        select(func.max(Block.created_at)).where(
            Block.project_id == project_id,
            Block.kind == BlockKind.message,
            Block.conversation_id.in_(
                await _conversations(session, project_id, room_ids, task_ids=task_ids)
            ),
        )
    )
    major = "|".join(
        str(part)
        for part in (
            overview.version if overview is not None else 0,
            *tasks,
            accepted,
        )
    )
    return Marks(major=major, minor=str(said))


async def _conversations(
    session: AsyncSession,
    project_id: uuid.UUID,
    room_ids: list[uuid.UUID],
    *,
    task_ids: list[uuid.UUID] | None = None,
) -> list[uuid.UUID]:
    """这些频道里所有对话的 id：频道主线、频道里的任务、频道里的支线。"""
    if not room_ids:
        return []
    tasks = (
        task_ids
        if task_ids is not None
        else list(
            await session.scalars(
                select(_tasks.c.id).where(
                    _tasks.c.project_id == project_id,
                    _tasks.c.room_id.in_(room_ids),
                )
            )
        )
    )
    threads = await session.scalars(
        select(Thread.id).where(
            Thread.project_id == project_id, Thread.room_id.in_(room_ids)
        )
    )
    return [*room_ids, *tasks, *threads]


@dataclass(frozen=True)
class Material:
    #: 交给模型的全文。
    text: str
    #: 项目目标写了什么（去掉首尾空白）；没写是空串。
    goal: str
    #: 近两周既没做完什么、也没人在做什么、也没人说话：没东西可写。
    quiet: bool


async def _names(session: AsyncSession, handles: set[str]) -> dict[str, str]:
    if not handles:
        return {}
    rows = await session.execute(
        select(User.username, UserProfile.nickname)
        .join(UserProfile, UserProfile.user_id == User.id)
        .where(User.username.in_(handles))
    )
    return {username: nickname for username, nickname in rows if nickname}


async def gather(
    session: AsyncSession, project_id: uuid.UUID, *, now: datetime | None = None
) -> Material:
    now = now or datetime.now(UTC)
    since = now - WINDOW
    rooms = await public_rooms(session, project_id)
    room_ids = list(rooms.names)
    overview = await pointed_overview(session, project_id)
    goal = (overview.content if overview is not None else "").strip()

    tasks = await _task_rows(session, project_id, room_ids)
    by_id = {task.id: task for task in tasks}
    open_tasks = sorted(
        (t for t in tasks if t.open),
        key=lambda t: t.updated_at or t.created_at,
        reverse=True,
    )
    doc_ids = [t.document_id for t in open_tasks if t.document_id]
    docs: dict[uuid.UUID, str] = {}
    if doc_ids:
        rows = await session.execute(
            select(Document.id, Document.content).where(Document.id.in_(doc_ids))
        )
        docs = {doc_id: content for doc_id, content in rows}
    accepted = (
        list(
            await session.execute(
                select(
                    _cards.c.task_id,
                    _cards.c.change_subject,
                    _cards.c.deliverable_name,
                )
                .where(
                    _cards.c.task_id.in_(list(by_id)),
                    _cards.c.status == STATUS_ACCEPTED,
                    _cards.c.decided_at > since,
                )
                .order_by(_cards.c.decided_at.desc())
            )
        )
        if by_id
        else []
    )

    talk_rooms = await _talk(
        session,
        project_id,
        room_ids,
        since,
        task_rooms={t.id: t.room_id for t in tasks},
    )
    handles = {author for said in talk_rooms.values() for author, _, _ in said}
    names = await _names(session, handles)

    found_proj, cached_proj = (
        recall(session, ("project", project_id))
        if isinstance(session, AsyncSession)
        else (False, None)
    )
    name = (
        cached_proj.name
        if found_proj and cached_proj is not None
        else await session.scalar(select(Project.name).where(Project.id == project_id))
    )
    lines = [f"# 项目：{name or ''}", f"今天：{now:%Y-%m-%d}", ""]
    lines += ["## 项目目标（人写的项目总览文档）", _clip(goal) or "（空）", ""]

    lines.append(
        "## 近两周做完、合进项目的事（按频道分，频道大致就是项目里的一块领域；"
        "「要做的」是开工时起的标题，「做成的」是交付时写的改动标题，以后者为准）"
    )
    done: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for task_id, subject, deliverable in accepted:
        task = by_id.get(task_id)
        if task is None:
            continue
        done[rooms.names[task.room_id]][task.title].append(subject or deliverable or "")
    for room, titles in sorted(done.items(), key=lambda item: -len(item[1])):
        lines.append(f"### #{room}（{len(titles)} 件）")
        for title, subjects in titles.items():
            lines.append(f"- 要做的：{title}")
            lines.extend(
                f"  做成的：{s}" for s in dict.fromkeys(s for s in subjects if s)
            )
    if not done:
        lines.append("（没有）")

    lines += ["", "## 正在做的事（还没做完的，按频道分）"]
    going: dict[str, list[TaskRow]] = defaultdict(list)
    for task in open_tasks:
        going[rooms.names[task.room_id]].append(task)
    for room, listed in sorted(going.items(), key=lambda item: -len(item[1])):
        lines.append(f"### #{room}（{len(listed)} 件）")
        for task in listed:
            head = _cut(
                docs.get(task.document_id) if task.document_id else "", _DOC_HEAD_CHARS
            )
            lines.append(f"- 「{task.title}」" + (f"：{head}" if head else ""))
    if not going:
        lines.append("（没有）")

    lines += [
        "",
        f"## 各频道近两周人说的话（每个频道最后 {TALK_PER_ROOM} 条，旧的在前）",
    ]
    for room_id, said in talk_rooms.items():
        lines.append(f"### #{rooms.names[room_id]}")
        for author, content, at in said:
            who = names.get(author, author)
            lines.append(f"- {at:%m-%d} {who}：{_cut(content, _LINE_CHARS)}")
    if not talk_rooms:
        lines.append("（没有）")

    return Material(
        text="\n".join(lines),
        goal=goal,
        quiet=not (done or going or talk_rooms),
    )


def _clip(goal: str) -> str:
    return goal if len(goal) <= _GOAL_CHARS else goal[:_GOAL_CHARS] + "…"


async def _talk(
    session: AsyncSession,
    project_id: uuid.UUID,
    room_ids: list[uuid.UUID],
    since: datetime,
    *,
    task_rooms: dict[uuid.UUID, uuid.UUID] | None = None,
) -> dict[uuid.UUID, list[tuple[str, str, datetime]]]:
    """每个频道近两周人说的最后几条，旧的在前；没人说话的频道不在里面。"""
    if not room_ids:
        return {}
    room_of: dict[uuid.UUID, uuid.UUID] = {room: room for room in room_ids}
    if task_rooms is not None:
        room_of.update(task_rooms)
    else:
        for task_id, room_id in await session.execute(
            select(_tasks.c.id, _tasks.c.room_id).where(
                _tasks.c.project_id == project_id, _tasks.c.room_id.in_(room_ids)
            )
        ):
            room_of[task_id] = room_id
    for thread_id, room_id in await session.execute(
        select(Thread.id, Thread.room_id).where(
            Thread.project_id == project_id, Thread.room_id.in_(room_ids)
        )
    ):
        room_of[thread_id] = room_id
    rows = await session.execute(
        select(Block.conversation_id, Block.author, Block.content, Block.created_at)
        .where(
            Block.project_id == project_id,
            Block.kind == BlockKind.message,
            Block.created_at > since,
            Block.conversation_id.in_(list(room_of)),
        )
        .order_by(Block.created_at.desc())
        .limit(_TALK_ROWS)
    )
    per: dict[uuid.UUID, list[tuple[str, str, datetime]]] = defaultdict(list)
    for conversation_id, author, content, at in rows:
        if not author or looks_like_agent_handle(author) or not (content or "").strip():
            continue
        said = per[room_of[conversation_id]]
        if len(said) < TALK_PER_ROOM:
            said.append((author, content, at))
    return {room: list(reversed(said)) for room, said in per.items()}


async def library_names(session: AsyncSession, project_id: uuid.UUID) -> list[str]:
    """资料库里现在有的文件名，按名字排。"""
    return [
        name
        for name, _, _ in await library_records.stored(
            # 项目自己的文件；题目的材料（`题目/`）归题目。
            session,
            Shelf.of_project(project_id, with_task=False),
        )
    ]
