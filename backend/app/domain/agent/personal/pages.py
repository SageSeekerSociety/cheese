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

from app.auth import task_roles as task_seats
from app.core.errors import NotFoundError
from app.domain.agent.personal import billing, people
from app.domain.agent.personal.prompt import task_brief
from app.domain.assistant import refs
from app.domain.block.queries import latest_messages
from app.domain.library import records as library_records
from app.domain.library.shelf import Shelf
from app.domain.living_doc.services import Documents
from app.domain.project import address
from app.domain.project.services import ProjectService
from app.domain.task import drafts, package
from app.domain.task.claims import claim_of
from app.domain.task.services import TaskService, ensure_task_readable
from app.domain.team.services import team_service


class PageAccess(Protocol):
    """Whether the asker may open a project's page, judged as opening it
    would be (the request's own resolver). Each raises what that page would."""

    async def topic(self, topic_id: uuid.UUID) -> Any:
        """A channel, a task or a 支线: its ``Place``."""

    async def document(self, document_id: uuid.UUID) -> None: ...

    async def project(self, project_id: uuid.UUID) -> None: ...

    async def team(self, team_id: int) -> Any:
        """A team the person is a member of: its ``Team``."""


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
    #: What the person calls it, for where a question was asked from.
    label: str = ""


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


def judge(reader: Reader) -> PageAccess:
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
    files = await package.files(reader.session, task.id)
    return Shown(
        task_brief(
            task,
            claim=await claim_of(reader.session, task=task, user_id=reader.user_id),
            timezone=reader.timezone,
            materials=[f.name for f in files],
            materials_readable=await task_seats.TASK.may(
                reader.session, task, reader.user_id, task_seats.Permission.download
            ),
        ),
        label=f"题目「{task.name}」",
    )


async def _draft(reader: Reader, page_id: str) -> Shown:
    """A draft's page (the publish page, or the edit page of a published 题目):
    the whole draft, and what the space lets it choose from."""
    row = await drafts.mine(reader.session, reader.user_id, _number(page_id))
    return Shown(
        await drafts.context(reader.session, reader.spaces, row, reader.timezone),
        label="发布题目",
    )


#: What 芝士 is told of a question asked from no page: whatever page it read
#: before is not where the person is now.
NO_PAGE = (
    "<这一页>\n这一问用户没有附上页面（他把当前页面关掉了，或在一个你读不到的页面上）。"
    "他说「这里」「这个」「这页」时，你不知道指哪：先问他指哪里，"
    "不要拿之前读到的页面或你猜的地方来答。\n</这一页>"
)

#: How much of a text 芝士 has no tool to read goes with it.
EXCERPT_CHARS = 6000
#: How many of a library's files its page lists.
LIBRARY_LISTED = 30


def excerpt(text: str) -> str:
    """The start of a text 芝士 cannot read by a ref, whole when it is short."""
    cut = text[:EXCERPT_CHARS]
    more = "\n……（后面还有，没有给你）" if len(text) > EXCERPT_CHARS else ""
    return f"<正文>\n{cut}{more}\n</正文>"


def size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


async def _library(reader: Reader, page_id: str) -> Shown:
    """A team's library: its newest files by name. The person's own is read
    by ``library/<名字>``; another team's files are only named."""
    team = await judge(reader).team(_number(page_id))
    own = await team_service(reader.session).ensure_personal_team(reader.user_id)
    rows = await library_records.newest(
        reader.session, Shelf.of_team(team.id), LIBRARY_LISTED
    )
    files = [f"- {row.name}（{size(row.bytes)}）" for row in rows]
    if team.id == own.id:
        what = "用户自己的资料库：用户在看里面的文件。"
        how = "- 读一份用 cheese_read library/<名字>，列出全部用 cheese_read library。"
    else:
        what = f"团队「{team.name}」的资料库：用户在看里面的文件。"
        how = "- 你没有读团队资料库文件的工具；用户要你看哪一份，请他附上或引用它。"
    return Shown(
        "\n".join(
            [
                "<这一页>",
                what,
                how,
                "</这一页>",
                f"<最新的 {len(files)} 份>\n" + "\n".join(files) + "\n</最新>"
                if files
                else "<还没有文件>",
            ]
        ),
        label="我的资料库" if team.id == own.id else f"团队「{team.name}」的资料库",
    )


#: How many of a conversation's latest messages its page says.
RECENT = 12
#: How much of one message it says.
RECENT_CHARS = 200


async def _project_line(reader: Reader, project_id: uuid.UUID) -> str:
    project = await ProjectService(reader.session).get(project_id)
    if project is None:
        return ""
    return f"<项目「{project.name}」 编号 {project.slug}>"


async def ref_of(
    reader: Reader, kind: address.Numbered, thing_id: uuid.UUID, project_id: uuid.UUID
) -> str:
    """The thing's ref (`assistant.refs`): what 芝士 reads it by."""
    project = await ProjectService(reader.session).get(project_id)
    found = await address.thing_by_id(reader.session, kind, thing_id)
    slug = project.slug if project is not None else str(project_id)
    number = found.number if found is not None else None
    return {
        address.Numbered.channel: refs.channel,
        address.Numbered.task: refs.task,
        address.Numbered.document: refs.doc,
    }[kind](slug, number)


async def named_place(reader: Reader, place) -> tuple[str, str, str]:
    """A channel, a task or a 支线 as 芝士 is told it, the ref it reads it by,
    and its short name."""
    if place.task is not None:
        here = await ref_of(
            reader, address.Numbered.task, place.task.id, place.project_id
        )
        label = f"任务「{place.task.title}」"
        return f"{label}（{here}，在频道「{place.room.title}」里）", here, label
    here = await ref_of(
        reader, address.Numbered.channel, place.room.id, place.project_id
    )
    if place.thread is not None:
        label = f"支线「{place.thread.title}」"
        return f"{label}（在频道「{place.room.title}」（{here}）里）", here, label
    label = f"频道「{place.room.title}」"
    return f"{label}（{here}）", here, label


async def _topic(reader: Reader, page_id: str) -> Shown:
    """A channel, a task or a 支线 of a project: what it is, and its latest
    messages; the rest is read by its ref."""
    place = await judge(reader).topic(_uuid(page_id))
    what, here, label = await named_place(reader, place)
    latest = await latest_messages(reader.session, place.conversation_id, RECENT)
    named = await people.names(reader.session, ((b.author, b.content) for b in latest))
    lines = [
        f"{named.of(b.author)}：{named.said(' '.join(b.content.split()))[:RECENT_CHARS]}"
        for b in latest
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
                f"- 用户说「这里」，指它；读更早的记录用 cheese_read {here}。",
                "</这一页>",
                await _project_line(reader, place.project_id),
                recent,
            ]
        ),
        project=place.project_id,
        label=label,
    )


async def _document(reader: Reader, page_id: str) -> Shown:
    """A document of a project: its title and ref; its text is read when it
    is needed. A team's own document has no ref 芝士 reads by, so the start of
    its text goes with it."""
    document_id = _uuid(page_id)
    await judge(reader).document(document_id)
    doc = await Documents(reader.session).get(document_id)
    if doc is None:
        raise NotFoundError.for_resource("document", page_id)
    if doc.project_id is None:
        team = await judge(reader).team(doc.team_id)
        return Shown(
            "\n".join(
                [
                    "<这一页>",
                    f"团队「{team.name}」的文档《{doc.title or '未命名'}》：用户在看这份文档。",
                    "- 用户说「这份」「文档」，指它。你没有读团队文档的工具，下面是它的正文"
                    + ("开头" if len(doc.content) > EXCERPT_CHARS else "")
                    + "。",
                    "</这一页>",
                    excerpt(doc.content),
                ]
            ),
            label=f"《{doc.title or '未命名'}》",
        )
    here = await ref_of(reader, address.Numbered.document, doc.id, doc.project_id)
    return Shown(
        "\n".join(
            [
                "<这一页>",
                f"文档《{doc.title or '未命名'}》（{here}）：用户在看这份文档。",
                f"- 用户说「这份」「文档」，指它；读全文用 cheese_read {here}。"
                "要改它，开任务交给项目的 AI 队友。",
                "</这一页>",
                await _project_line(reader, doc.project_id),
            ]
        ),
        project=doc.project_id,
        label=f"《{doc.title or '未命名'}》",
    )


async def _project(reader: Reader, page_id: str) -> Shown:
    """Any other page of a project (its overview, board, library, members):
    which project; what is in it is read by its ref."""
    project_id = _uuid(page_id)
    await judge(reader).project(project_id)
    project = await ProjectService(reader.session).get(project_id)
    return Shown(
        "\n".join(
            [
                "<这一页>",
                "项目里的一页：用户在这个项目里。",
                "- 用户说「这个项目」，指它；它的频道、任务和文档用 cheese_read 读项目编号，"
                "找东西用 cheese_find 加上 where。",
                "</这一页>",
                await _project_line(reader, project_id),
            ]
        ),
        project=project_id,
        label=f"项目「{project.name}」" if project is not None else "",
    )


PAGES: dict[str, Page] = {
    "task": Page(_task),
    # Writing a 题目 is the heavier use, and is billed as such.
    "draft": Page(_draft, billing=billing.WRITING),
    "topic": Page(_topic),
    "doc": Page(_document),
    "project": Page(_project),
    "library": Page(_library),
}

#: The kinds a question may name, for the request's schema. Written out
#: because a type is read before the table is built; a test holds it to
#: ``PAGES``.
PageKind = Literal["task", "draft", "topic", "doc", "project", "library"]


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
