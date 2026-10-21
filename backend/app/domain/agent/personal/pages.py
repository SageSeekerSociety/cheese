"""The page a question is asked from, as 芝士 is told it.

The page sends only what it is (``kind``) and which one (``id``); what 芝士
reads is written here, from the rows, for the person asking and with the same
judgment as opening that page — a person who cannot open a page cannot ask
about it either. Nothing the browser renders reaches the prompt.

Each kind is one line in ``PAGES``: how the page is written out, and which
kind of use it is billed as. Teaching 芝士 a new page is a renderer and a line
here; the route that asks does not change.

A page in a project (a channel or a task, a document, any other page of the
project) also says which project it is in (``Shown.project``): the question's
tools are let into that project, as the person, and into no other. Whether the
person may open such a page is judged where the request is (``PageAccess``),
by the same judge the page itself is opened with.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.storage import get_storage_backend
from app.domain.agent.personal import billing
from app.domain.agent.personal.prompt import task_brief
from app.domain.block.queries import latest_messages
from app.domain.living_doc.services import Documents
from app.domain.project.services import ProjectService
from app.domain.task import drafts
from app.domain.task.attachment_service import TaskAttachmentService
from app.domain.task.claims import claim_of
from app.domain.task.services import TaskService, ensure_task_readable


class PageAccess(Protocol):
    """Whether the asker may open a project's page, judged as opening it
    would be (the request's own resolver). Each raises what that page would."""

    async def topic(self, topic_id: uuid.UUID) -> Any:
        """A channel, a task or a 支线: its ``Place``."""

    async def document(self, document_id: uuid.UUID) -> None: ...

    async def project(self, project_id: uuid.UUID) -> None: ...


@dataclass(frozen=True)
class Reader:
    """Who is asking, and what reading a page for them needs."""

    session: AsyncSession
    user_id: int
    #: The asker's clock, the one the page shows times on.
    timezone: str | None
    spaces: drafts.SpaceReads
    #: Judges a project's pages; without it they cannot be read.
    access: PageAccess | None = None


@dataclass(frozen=True)
class Shown:
    """A page as 芝士 is told it, and the project it is in, if any."""

    text: str
    project: uuid.UUID | None = None


@dataclass(frozen=True)
class Page:
    render: Callable[[Reader, str], Awaitable[Shown]]
    #: What a question asked here is billed as.
    billing: str = billing.ASKING


def _number(page_id: str) -> int:
    if not page_id.isdigit():
        raise NotFoundError.for_resource("page", page_id)
    return int(page_id)


def _uuid(page_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(page_id)
    except ValueError as exc:
        raise NotFoundError.for_resource("page", page_id) from exc


def _access(reader: Reader) -> PageAccess:
    if reader.access is None:
        raise NotFoundError.for_resource("page", "project")
    return reader.access


async def _task(reader: Reader, page_id: str) -> Shown:
    """A 题目's page (问芝士 on the task page): the task as anyone who can open
    it reads it, the asker's own claim, and its materials by name."""
    task_id = _number(page_id)
    task = await TaskService.of(reader.session).get_task(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    await ensure_task_readable(
        session=reader.session, task=task, user_id=reader.user_id
    )
    files, _, readable = await TaskAttachmentService(
        session=reader.session, storage=get_storage_backend()
    ).list_for_task(task=task, user_id=reader.user_id)
    return Shown(
        task_brief(
            task,
            claim=await claim_of(reader.session, task=task, user_id=reader.user_id),
            timezone=reader.timezone,
            materials=[
                (f.id, (f.meta or {}).get("filename") or str(f.id)) for f in files
            ],
            materials_readable=readable,
        )
    )


async def _draft(reader: Reader, page_id: str) -> Shown:
    """A draft's page (the publish page, or the edit page of a published 题目):
    the whole draft, and what the space lets it choose from."""
    row = await drafts.mine(reader.session, reader.user_id, _number(page_id))
    return Shown(
        await drafts.context(reader.session, reader.spaces, row, reader.timezone)
    )


#: How many of a conversation's latest messages its page says.
RECENT = 12
#: How much of one message it says.
RECENT_CHARS = 200

#: What the person's 芝士 does in a project, said with each of its pages.
IN_PROJECT = (
    "- 在项目里你只读、起草、派活：发消息用 cheese_message_draft、开任务用 "
    "cheese_task_draft 起草，用户在面板里确认后才发出去。不要改项目里的文档和代码；"
    "要改，开任务交给项目的 AI 队友。"
)


async def _project_line(reader: Reader, project_id: uuid.UUID) -> str:
    project = await ProjectService(reader.session).get(project_id)
    name = project.name if project is not None else ""
    return f"<项目 {name} id={project_id}>"


async def _topic(reader: Reader, page_id: str) -> Shown:
    """A channel, a task or a 支线 of a project: what it is, and its latest
    messages; the rest is read with the chat tools, by its id."""
    place = await _access(reader).topic(_uuid(page_id))
    if place.task is not None:
        what = f"任务「{place.task.title}」（在频道「{place.room.title}」里）"
    elif place.thread is not None:
        what = f"支线「{place.thread.title}」（在频道「{place.room.title}」里）"
    else:
        what = f"频道「{place.room.title}」"
    lines = [
        f"{b.author}：{' '.join(b.content.split())[:RECENT_CHARS]}"
        for b in await latest_messages(reader.session, place.conversation_id, RECENT)
    ]
    recent = (
        f"<最近 {len(lines)} 条>\n" + "\n".join(lines) + "\n</最近>"
        if lines
        else "<还没有消息>"
    )
    return Shown(
        "\n".join(
            [
                "<这一页>",
                f"{what}：用户在看它的对话。",
                f"- 用户说「这里」，指它。读更早的记录、搜别处的讨论用聊天工具，topic 传 "
                f"{place.conversation_id}。",
                IN_PROJECT,
                "</这一页>",
                await _project_line(reader, place.project_id),
                recent,
            ]
        ),
        project=place.project_id,
    )


async def _document(reader: Reader, page_id: str) -> Shown:
    """A document of a project: its title and where it is; its text is read
    with cheese_doc_get when it is needed."""
    document_id = _uuid(page_id)
    await _access(reader).document(document_id)
    doc = await Documents(reader.session).get(document_id)
    if doc is None or doc.project_id is None:
        raise NotFoundError.for_resource("document", page_id)
    return Shown(
        "\n".join(
            [
                "<这一页>",
                f"文档《{doc.title or '未命名'}》：用户在看这份文档。",
                f"- 用户说「这份」「文档」，指它。读全文用 cheese_doc_get，document 传 "
                f"{document_id}。",
                IN_PROJECT,
                "</这一页>",
                await _project_line(reader, doc.project_id),
            ]
        ),
        project=doc.project_id,
    )


async def _project(reader: Reader, page_id: str) -> Shown:
    """Any other page of a project (its overview, board, library, members):
    which project; what is in it is looked up with the tools."""
    project_id = _uuid(page_id)
    await _access(reader).project(project_id)
    return Shown(
        "\n".join(
            [
                "<这一页>",
                "项目里的一页：用户在这个项目里。",
                "- 用户说「这个项目」，指它。项目里的频道、讨论和文档用 "
                "cheese_project_search 找。",
                IN_PROJECT,
                "</这一页>",
                await _project_line(reader, project_id),
            ]
        ),
        project=project_id,
    )


PAGES: dict[str, Page] = {
    "task": Page(_task),
    # Writing a 题目 is the heavier use, and is billed as such.
    "draft": Page(_draft, billing=billing.WRITING),
    "topic": Page(_topic),
    "doc": Page(_document),
    "project": Page(_project),
}

#: The kinds a question may name, for the request's schema. Written out
#: because a type is read before the table is built; a test holds it to
#: ``PAGES``.
PageKind = Literal["task", "draft", "topic", "doc", "project"]


async def render(reader: Reader, kind: str, page_id: str) -> Shown:
    """The page written out for 芝士. Raises what opening the page would: a
    page the asker cannot see is not found."""
    return await PAGES[kind].render(reader, page_id)


def billed_as(kind: str | None) -> str:
    """What a question asked from a page of ``kind`` is billed as; one asked
    from no page is a plain question."""
    return billing.ASKING if kind is None else PAGES[kind].billing


def home_page(place_kind: str, place_id: str) -> tuple[PageKind, str] | None:
    """The page a conversation was started on, which a question asked without
    naming one is taken to be asked from."""
    # Conversations were started on a 题目 or a draft before they followed the
    # person across pages; none is started on any other page.
    if place_kind in ("task", "draft") and place_id.isdigit():
        return cast(PageKind, place_kind), place_id
    return None
