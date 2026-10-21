"""The page a question is asked from, as 芝士 is told it.

The page sends only what it is (``kind``) and which one (``id``); what 芝士
reads is written here, from the rows, for the person asking and with the same
judgment as opening that page — a person who cannot open a page cannot ask
about it either. Nothing the browser renders reaches the prompt.

Each kind is one line in ``PAGES``: how the page is written out, and which
kind of use it is billed as. Teaching 芝士 a new page is a renderer and a line
here; the route that asks does not change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.storage import get_storage_backend
from app.domain.agent.personal import billing
from app.domain.agent.personal.prompt import task_brief
from app.domain.task import drafts
from app.domain.task.attachment_service import TaskAttachmentService
from app.domain.task.claims import claim_of
from app.domain.task.services import TaskService, ensure_task_readable


@dataclass(frozen=True)
class Reader:
    """Who is asking, and what reading a page for them needs."""

    session: AsyncSession
    user_id: int
    #: The asker's clock, the one the page shows times on.
    timezone: str | None
    spaces: drafts.SpaceReads


@dataclass(frozen=True)
class Page:
    render: Callable[[Reader, int], Awaitable[str]]
    #: What a question asked here is billed as.
    billing: str = billing.ASKING


async def _task(reader: Reader, task_id: int) -> str:
    """A 题目's page (问芝士 on the task page): the task as anyone who can open
    it reads it, the asker's own claim, and its materials by name."""
    task = await TaskService.of(reader.session).get_task(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    await ensure_task_readable(
        session=reader.session, task=task, user_id=reader.user_id
    )
    files, _, readable = await TaskAttachmentService(
        session=reader.session, storage=get_storage_backend()
    ).list_for_task(task=task, user_id=reader.user_id)
    return task_brief(
        task,
        claim=await claim_of(reader.session, task=task, user_id=reader.user_id),
        timezone=reader.timezone,
        materials=[(f.id, (f.meta or {}).get("filename") or str(f.id)) for f in files],
        materials_readable=readable,
    )


async def _draft(reader: Reader, draft_id: int) -> str:
    """A draft's page (the publish page, or the edit page of a published 题目):
    the whole draft, and what the space lets it choose from."""
    row = await drafts.mine(reader.session, reader.user_id, draft_id)
    return await drafts.context(reader.session, reader.spaces, row, reader.timezone)


PAGES: dict[str, Page] = {
    "task": Page(_task),
    # Writing a 题目 is the heavier use, and is billed as such.
    "draft": Page(_draft, billing=billing.WRITING),
}

#: The kinds a question may name, for the request's schema. Written out
#: because a type is read before the table is built; a test holds it to
#: ``PAGES``.
PageKind = Literal["task", "draft"]


async def render(reader: Reader, kind: str, page_id: int) -> str:
    """The page written out for 芝士. Raises what opening the page would: a
    page the asker cannot see is not found."""
    return await PAGES[kind].render(reader, page_id)


def billed_as(kind: str | None) -> str:
    """What a question asked from a page of ``kind`` is billed as; one asked
    from no page is a plain question."""
    return billing.ASKING if kind is None else PAGES[kind].billing


def home_page(place_kind: str, place_id: str) -> tuple[PageKind, int] | None:
    """The page a conversation was started on, which a question asked without
    naming one is taken to be asked from."""
    if place_kind in PAGES and place_id.isdigit():
        return cast(PageKind, place_kind), int(place_id)
    return None
