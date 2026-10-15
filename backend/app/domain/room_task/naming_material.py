"""What the model reads: the task's conversation, as the ``<task>`` block.

`naming.py` decides when a task is worth asking about and writes what comes
back; this module is the text on the way in. `_material` collects what was said
— the discussion the task was turned from (`_origin`), then its own conversation
— and `_render` turns those lines into the prompt's ``<task>``: escaped, cut to
a character budget with the newest lines kept first, and followed by the
instruction for the stage (`_STAGE_ASK`).

The split is by size, not by layering (`naming.py` was 922 lines): nothing here
opens a gateway call, and every name it defines is re-exported by `naming.py`,
so no call point moved.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.identity.handles import names_a_person
from app.domain.living_doc.services import Documents
from app.domain.room_task.models import Task, TaskTitleSource
from app.domain.thread.models import Thread

# The whole prompt stays small: a title needs the gist, not the transcript.
_MESSAGES = 12
_MESSAGE_CHARS = 400
_CONVERSATION_CHARS = 2400
_GOAL_CHARS = 600

_STAGE_ASK = {
    "name": "这个任务还没有名字，给它起一个。keep 填 false。",
    "calibrate": (
        "当前标题是只看开场第一句话起的。结合现在的对话判断它是否准确："
        "准确且具体就保留（keep=true，title 照抄当前标题）；"
        "不准确或太笼统才换一个（keep=false）。"
    ),
    "follow": (
        "判断这个任务的方向是否已经变了。当前标题仍能让人认出这件事，"
        "就保留（keep=true，title 照抄当前标题）。只有讨论的对象已经换了"
        "（换了系统、换了问题、从一件事转到另一件事）才换（keep=false）。"
        "措辞更好、更完整都不是换的理由。"
    ),
}


@dataclass(frozen=True)
class Line:
    person: bool
    text: str


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# A mention is the platform's own markup (`<@handle>`, what chat.py resolves a
# mention from) or a bare `@handle` typed by hand. Only the handle goes: the
# rest of the sentence is what the person said. `@\S+` used to take everything
# up to the next whitespace, which in Chinese is the whole line — the opener of
# room b031c720 (71 characters, 「当我打开@的时候…」) was read as four, counted
# as an empty opener, and the room was never named (2026-09-27).
_MENTION = re.compile(r"<@[\w-]+>|@[\w-]+")


def _said(text: str) -> str:
    """What a message says once mentions are set aside."""
    return _MENTION.sub("", text).strip()


def _messages(blocks: list[Block]) -> list[Line]:
    """What people and agents said among ``blocks``, in the order given.

    Platform notices, tool activity and messages hidden from the conversation
    are not what the task is about."""
    lines: list[Line] = []
    for block in blocks:
        if block.kind != BlockKind.message:
            continue
        if block.author_type != AuthorType.participant:
            continue
        if (block.meta or {}).get("in_room") is False:
            continue
        text = (block.content or "").strip()
        if text:
            lines.append(Line(person=names_a_person(block.author), text=text))
    return lines


async def _newest(
    session: AsyncSession, conversation_id: uuid.UUID, before: datetime | None = None
) -> list[Block]:
    stmt = select(Block).where(
        Block.conversation_id == conversation_id,
        Block.kind == BlockKind.message,
        Block.author_type == AuthorType.participant,
    )
    if before is not None:
        stmt = stmt.where(Block.created_at < before)
    return list(
        (
            await session.scalars(
                stmt.order_by(Block.created_at.desc()).limit(_MESSAGES * 2)
            )
        ).all()
    )


async def _origin(session: AsyncSession, task: Task) -> list[Block]:
    """The discussion a task was turned from (转为任务), oldest first: the
    message, and what was said under it in its 支线 before the task existed.

    That message is where a person said what the task is. The task's own
    conversation starts after it, often with nothing but its AI teammate's
    words, so reading only that, a task made from a message was never named:
    every trigger found no person in it (2026-10-07)."""
    if task.upgraded_from_block_id is None:
        return []
    root = await session.get(Block, task.upgraded_from_block_id)
    if root is None:
        return []
    thread = await Thread.of_root(session, root.id)
    replies = (
        await _newest(session, thread.id, before=task.created_at)
        if thread is not None
        else []
    )
    return [root, *reversed(replies)]


async def _conversation(session: AsyncSession, task: Task) -> list[Line]:
    """What was said about the task, newest ``_MESSAGES`` messages, oldest
    first: the discussion it was turned from, then its own conversation."""
    own = _messages(list(reversed(await _newest(session, task.id))))
    lines = _messages(await _origin(session, task)) + own
    return lines[-_MESSAGES:]


def _render(
    *,
    stage: str,
    current: str | None,
    goal: str,
    lines: list[Line],
) -> str:
    budget = _CONVERSATION_CHARS
    rendered: list[str] = []
    # Newest first while cutting to budget, so the latest direction survives.
    for line in reversed(lines):
        text = line.text[:_MESSAGE_CHARS]
        if budget <= 0:
            break
        text = text[:budget]
        budget -= len(text)
        role = "person" if line.person else "agent"
        rendered.append(f'<message role="{role}">{_escape(text)}</message>')
    rendered.reverse()
    parts = ["<task>"]
    if current:
        parts.append(f"<current_title>{_escape(current)}</current_title>")
    if goal:
        parts.append(f"<goal>{_escape(goal[:_GOAL_CHARS])}</goal>")
    parts.append("<conversation>\n" + "\n".join(rendered) + "\n</conversation>")
    parts.append("</task>")
    return "\n".join(parts) + "\n\n" + _STAGE_ASK[stage]


async def _material(session: AsyncSession, task: Task, stage: str) -> str | None:
    lines = await _conversation(session, task)
    if not lines:
        return None
    doc = (
        await Documents(session).get(task.document_id)
        if task.document_id is not None
        else None
    )
    current = None if task.title_source == TaskTitleSource.placeholder else task.title
    return _render(
        stage=stage,
        current=current,
        goal=(doc.content or "").strip() if doc is not None else "",
        lines=lines,
    )
