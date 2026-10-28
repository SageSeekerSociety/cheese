"""A thing, read by its ref, in the words a person's 芝士 is given it.

``cheese_read`` names a thing the way its page's address does
(`assistant.refs`); this module writes that thing out: what it is, where it is,
what is in it, and the refs that read further. What is long comes in pages of
about ``PAGE_CHARS``, the last line saying how to read the next, so one read
never takes more of the conversation than it can give, and an earlier read
never needs to be taken back: reading a page again is how 芝士 gets it back.

Whether the person may open a thing is judged where the request is
(``Access``), as opening its page would be: a thing they cannot open reads as
not found.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.agent.personal import people
from app.domain.assistant import refs
from app.domain.assistant.refs import Ref
from app.domain.block.authorship import speaker
from app.domain.block.queries import messages_page
from app.domain.living_doc.services import Documents
from app.domain.notification.preferences import local_time
from app.domain.project import address
from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.queries import latest_cards_by_task
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.services import TaskService as RoomTasks

#: About how much one page of a long thing says.
PAGE_CHARS = 1500
#: How many messages one page of a conversation says.
MESSAGES = 20
#: How much of one message a page says; the whole of it is a page of its own.
MESSAGE_CHARS = 300
#: How many of a project's channels, tasks and documents its own page lists.
LISTED = 15


class Access(Protocol):
    """Whether the person may open a thing, judged as opening its page would
    be. Each raises what that page would."""

    async def topic(self, topic_id: uuid.UUID) -> Any:
        """A channel, a task or a 支线: its ``Place``."""

    async def document(self, document_id: uuid.UUID) -> None: ...

    async def project(self, project_id: uuid.UUID) -> None: ...

    async def channels(self, project_id: uuid.UUID) -> Sequence[Any]:
        """The project's channels the person may open, as ``Topic`` rows."""
        ...


@dataclass(frozen=True)
class Reader:
    """Who is reading: the person, and the clock their pages show times on."""

    session: AsyncSession
    user_id: int
    timezone: str | None


@dataclass(frozen=True)
class Read:
    """What a ref read: ``subject`` names it the way the person knows it
    (「频道「综合」」), and is what 芝士's step shows in place of the ref;
    ``text`` is it written out for 芝士."""

    subject: str
    text: str


def _when(reader, moment) -> str:
    return f"{local_time(moment, reader.timezone):%Y-%m-%d %H:%M}"


def _paged(text: str, ref: str, page: int) -> str:
    """``text``'s ``page``-th part of about ``PAGE_CHARS``, cut at a line where
    one is near, and how to read on."""
    parts: list[str] = []
    rest = text
    while rest:
        if len(rest) <= PAGE_CHARS:
            parts.append(rest)
            break
        cut = rest.rfind("\n", PAGE_CHARS // 2, PAGE_CHARS)
        cut = cut + 1 if cut > 0 else PAGE_CHARS
        parts.append(rest[:cut])
        rest = rest[cut:]
    if not parts:
        return "（空）"
    if not 1 <= page <= len(parts):
        return f"没有第 {page} 段：一共 {len(parts)} 段。"
    body = parts[page - 1].rstrip()
    if len(parts) == 1:
        return body
    head = f"（第 {page}/{len(parts)} 段）"
    tail = (
        f"\n（下一段：读 {ref}，page={page + 1}）"
        if page < len(parts)
        else "\n（已读完）"
    )
    return f"{head}\n{body}{tail}"


async def _project_of(reader: Reader, slug: str | None) -> Project:
    project = await address.project_by_ref(reader.session, slug or "")
    if project is None:
        raise NotFoundError.for_resource("project", slug or "")
    return project


async def _numbered(
    reader: Reader, ref: Ref, kind: address.Numbered
) -> tuple[Project, address.Addressed]:
    project = await _project_of(reader, ref.slug)
    found = await address.thing_by_number(
        reader.session, project.id, kind, ref.number or 0
    )
    if found is None:
        raise NotFoundError.for_resource(kind.value, ref.number or 0)
    return project, found


async def _messages(reader: Reader, conversation_id: uuid.UUID, page: int) -> str:
    rows, total = await messages_page(
        reader.session,
        conversation_id,
        limit=MESSAGES,
        skip=(page - 1) * MESSAGES,
    )
    if not rows:
        return "（还没有消息）" if page == 1 else f"没有第 {page} 页的消息了。"
    named = await people.names(reader.session, ((b.author, b.content) for b in rows))
    lines = []
    for block in rows:
        text = named.said(" ".join(block.content.split()))
        if len(text) > MESSAGE_CHARS:
            text = text[:MESSAGE_CHARS] + "……"
        lines.append(
            f"[{_when(reader, block.created_at)}] "
            f"{speaker(block, named.of(block.author))}：{text}"
        )
    shown_from = total - (page - 1) * MESSAGES - len(rows) + 1
    head = (
        f"消息 {shown_from}–{shown_from + len(rows) - 1}（共 {total} 条，旧的在前）："
    )
    more = f"\n（更早的消息：同一个编号，page={page + 1}）" if shown_from > 1 else ""
    return head + "\n" + "\n".join(lines) + more


async def _channel(reader: Reader, access: Access, ref: Ref, page: int) -> Read:
    project, found = await _numbered(reader, ref, address.Numbered.channel)
    place = await access.topic(found.id)
    room = place.room
    lines = [
        f"频道「{room.title}」（{refs.channel(project.slug, found.number)}），"
        f"项目「{project.name}」（{project.slug}）",
    ]
    if room.description:
        lines.append(f"说明：{room.description}")
    if page == 1:
        tasks = [
            t
            for t in await RoomTasks(reader.session).list_in_room(room.id)
            if t.status == TaskStatus.open
        ]
        if tasks:
            lines.append("进行中的任务：")
            lines += [
                f"- 任务「{t.title}」（{refs.task(project.slug, t.number)}）"
                for t in tasks[:LISTED]
            ]
    lines.append(await _messages(reader, place.conversation_id, page))
    return Read(f"频道「{room.title}」", "\n".join(lines))


async def _task(reader: Reader, access: Access, ref: Ref, page: int) -> Read:
    project, found = await _numbered(reader, ref, address.Numbered.task)
    place = await access.topic(found.id)
    task = place.task
    state = "进行中" if task.status == TaskStatus.open else "已结束"
    lines = [
        f"任务「{task.title}」（{refs.task(project.slug, found.number)}），{state}，"
        f"在频道「{place.room.title}」"
        f"（{refs.channel(project.slug, place.room.number)}），"
        f"项目「{project.name}」",
        f"负责人：{task.owner_handle or '无'}；AI 队友：{task.agent_handle or '无'}",
    ]
    if task.conclusion:
        lines.append(f"结论：{task.conclusion}")
    card = (await latest_cards_by_task(reader.session, [task.id])).get(task.id)
    if card is not None:
        lines.append(
            f"审阅：{_STATUS.get(card.status, card.status)}，由 "
            f"{'、'.join(card.reviewer_handles) or '未定的人'} 审阅；"
            f"读：{refs.review(project.slug, found.number)}"
        )
    lines.append(await _messages(reader, place.conversation_id, page))
    return Read(f"任务「{task.title}」", "\n".join(lines))


_STATUS: dict[str, str] = {
    AcceptStatus.pending: "等审阅",
    AcceptStatus.accepted: "已采纳",
    AcceptStatus.rejected: "已退回",
    AcceptStatus.revoked: "已撤回",
    AcceptStatus.conflict: "采纳时有冲突",
    AcceptStatus.pending_gate: "检查运行中",
    AcceptStatus.gate_failed: "检查未通过",
}


def checks_of(card: AcceptCard) -> str:
    """The card's checks, as a clause for an action naming the card: a person
    who asked for "the ones whose checks passed" is matched against it."""
    if card.status is AcceptStatus.gate_failed:
        return "，检查未通过"
    if card.status is AcceptStatus.pending_gate:
        return "，检查运行中"
    if card.gate_passed_at is not None:
        return "，检查已通过"
    return ""


async def _review(reader: Reader, access: Access, ref: Ref, page: int) -> Read:
    project, found = await _numbered(reader, ref, address.Numbered.task)
    place = await access.topic(found.id)
    latest = (await latest_cards_by_task(reader.session, [found.id])).get(found.id)
    subject = f"任务「{place.task.title}」的审阅"
    if latest is None:
        return Read(subject, f"任务「{place.task.title}」还没有提交审阅。")
    card = await reader.session.get(AcceptCard, latest.id)
    assert card is not None
    lines = [
        f"任务「{place.task.title}」的审阅（{refs.review(project.slug, found.number)}）",
        f"状态：{_STATUS.get(card.status, str(card.status))}；由 {card.reviewer_handle} 审阅",
    ]
    if card.decided_by:
        lines.append(f"由 {card.decided_by} 于 {_when(reader, card.decided_at)} 决定")
    if card.focus:
        lines.append(f"请审阅人看的重点：{card.focus}")
    if card.change_subject:
        lines.append(f"改动：{card.change_subject}")
    if card.change_body:
        lines.append(card.change_body)
    if card.deliverable_name or card.deliverable_url:
        lines.append(
            f"交付物：{card.deliverable_name or ''} {card.deliverable_url or ''}".strip()
        )
    if card.pr_url:
        lines.append(f"合并请求：{card.pr_url}")
    if card.note:
        lines.append(f"卡上的说明：{card.note}")
    if card.gate_passed_at is not None:
        lines.append(f"检查：{_when(reader, card.gate_passed_at)} 通过")
    if card.gate_output:
        lines.append("检查输出：\n" + card.gate_output)
    lines.append(
        "采纳或退回用 cheese_card(action=decide)；要看具体改了哪些文件，到任务页的「改动」。"
    )
    return Read(
        subject,
        _paged("\n".join(lines), refs.review(project.slug, found.number), page),
    )


async def _doc(reader: Reader, access: Access, ref: Ref, page: int) -> Read:
    project, found = await _numbered(reader, ref, address.Numbered.document)
    await access.document(found.id)
    doc = await Documents(reader.session).get(found.id)
    if doc is None:
        raise NotFoundError.for_resource("document", ref.number or 0)
    here = refs.doc(project.slug, found.number)
    head = f"文档《{doc.title or '未命名'}》（{here}），项目「{project.name}」\n"
    return Read(
        f"文档《{doc.title or '未命名'}》", head + _paged(doc.content or "", here, page)
    )


async def _project(reader: Reader, access: Access, ref: Ref, page: int) -> Read:
    project = await _project_of(reader, ref.slug)
    await access.project(project.id)
    slug = project.slug
    lines = [f"项目「{project.name}」（{slug}）"]
    channels = list(await access.channels(project.id))
    lines.append(f"频道（{len(channels)} 个）：")
    lines += [
        f"- 「{c.title}」（{refs.channel(slug, c.number)}）"
        for c in channels[:LISTED]
        if c.number is not None
    ]
    rooms = {c.id for c in channels}
    tasks = [
        t
        for t in await RoomTasks(reader.session).list_in_project(
            project.id, rooms=rooms, status=TaskStatus.open.value
        )
    ]
    lines.append(f"进行中的任务（{len(tasks)} 个）：")
    lines += [
        f"- 「{t.title}」（{refs.task(slug, t.number)}），负责人 {t.owner_handle or '无'}"
        for t in tasks[-LISTED:]
    ]
    docs = await Documents(reader.session).of_project(
        project.id, after=None, limit=LISTED
    )
    numbered = [d for d in docs if d.number is not None]
    lines.append("文档（最近改过的在前）：")
    lines += [
        f"- 《{d.title or '未命名'}》（{refs.doc(slug, d.number)}）"
        for d in numbered[:LISTED]
    ]
    return Read(f"项目「{project.name}」", _paged("\n".join(lines), slug, page))


Render = Callable[[Reader, Access, Ref, int], Awaitable[Read]]

#: What each kind of ref reads as.
KINDS: dict[str, Render] = {
    "project": _project,
    "channel": _channel,
    "task": _task,
    "review": _review,
    "doc": _doc,
}


async def read(reader: Reader, access: Access, ref: Ref, page: int = 1) -> Read:
    """The thing ``ref`` names, written out; raises not found for a kind this
    module does not read (the caller reads those elsewhere)."""
    render = KINDS.get(ref.kind)
    if render is None:
        raise NotFoundError.for_resource("ref", ref.kind)
    return await render(reader, access, ref, max(1, page))


async def latest_card(session: AsyncSession, task_id: uuid.UUID) -> AcceptCard | None:
    """The review card a task rides on now: its latest."""
    latest = (await latest_cards_by_task(session, [task_id])).get(task_id)
    return await session.get(AcceptCard, latest.id) if latest is not None else None


async def numbered(
    session: AsyncSession, ref: Ref, kind: address.Numbered
) -> tuple[Project, address.Addressed]:
    """The project and the numbered thing a project ref names; not found
    otherwise. Whether the person may see it is the caller's to judge."""
    project = await address.project_by_ref(session, ref.slug or "")
    if project is None:
        raise NotFoundError.for_resource("project", ref.slug or "")
    found = await address.thing_by_number(session, project.id, kind, ref.number or 0)
    if found is None:
        raise NotFoundError.for_resource(kind.value, ref.number or 0)
    return project, found
