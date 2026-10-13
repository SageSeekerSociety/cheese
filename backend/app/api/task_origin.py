"""Where a task came from: the message it was made from, the discussion
around it, and what was put on the table there — documents written or changed
there, files sent, files referred to.

A task made from a message knows it (``source_block_id``, and for one shown
under a main-line message ``upgraded_from_block_id``). The task page quotes
the message at its top and lists the rest as 相关, and the task's AI teammate
is handed the same list with the discussion when it drafts the document.
"""

import re
import uuid
from datetime import UTC, datetime

from app.api.topics_deps import BlockRepository
from app.domain.block.documents import DOC_CARD_KEY
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.identity.handles import recipient_seat
from app.domain.identity.services import IdentityService
from app.domain.room_task.models import Task
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic

#: How many messages before the one a task comes from count as its discussion.
#: The same window the opening instruction quotes (`task_instructions.py`).
DISCUSSION_BEFORE = 8
#: A file referred to in a message: ``<&path>``.
_FILE_REF = re.compile(r"<&([^>\s]+)>")
_SNIPPET = 200


def _snippet(block: Block | None) -> dict | None:
    return None if block is None else _said(block)


def _said(block: Block) -> dict:
    return {
        "block_id": str(block.id),
        "author": block.author,
        "content": block.content[:_SNIPPET],
        "created_at": block.created_at.isoformat(),
    }


async def _window(repo, conversation_id, until: datetime, before: int) -> list[Block]:
    earlier = await repo.messages_before(conversation_id, until, limit=before)
    since = earlier[0].created_at if earlier else until
    return await repo.between(conversation_id, since, until)


async def discussion(db, task) -> tuple[dict | None, list[Block]]:
    """The discussion ``task`` was made from — where it is, the message it
    hangs on, how many replies — and everything said in it. ``(None, [])`` for
    a task created on its own."""
    repo = BlockRepository(db)
    if task.upgraded_from_block_id is not None:
        root = await repo.get(task.upgraded_from_block_id)
        if root is None:
            return None, []
        blocks = await _window(
            repo, root.conversation_id, root.created_at, DISCUSSION_BEFORE
        )
        thread = await Thread.of_root(db, root.id)
        if thread is not None:
            blocks += await repo.between(thread.id, root.created_at, datetime.now(UTC))
        return {
            "conversation_id": str(thread.id if thread else root.conversation_id),
            "room_id": str(task.room_id),
            "root": _snippet(root),
            "reply_count": thread.reply_count if thread else 0,
        }, blocks
    return None, []


#: How far back a teammate's task looks for the message that asked for it.
ASKED_WITHIN = 20


async def asked_in(
    db, conversation_id: uuid.UUID, agent_handle: str, *, main_line: bool
) -> Block | None:
    """The person's message an AI teammate creating a task in this
    conversation is answering: the latest one handed to it. In a channel's
    main line only one that named it counts: everything said there is handed
    to the room's teammate, most of it people talking to each other. None when
    no person asked."""
    recent = await BlockRepository(db).messages_before(
        conversation_id, datetime.now(UTC), limit=ASKED_WITHIN
    )
    for block in reversed(recent):
        recipient = (block.meta or {}).get("agent_recipient") or {}
        if (
            block.author_type == AuthorType.participant
            and recipient_seat(recipient) == agent_handle
            and (recipient.get("mentioned") or not main_line)
            and not await IdentityService(db).is_agent(block.author)
        ):
            return block
    return None


async def source(db, task) -> dict | None:
    """The message ``task`` was made from, quoted, with the conversation it is
    in so the page can open it there. None for a task created on its own."""
    block_id = task.source_block_id or task.upgraded_from_block_id
    block = await BlockRepository(db).get(block_id) if block_id else None
    if block is None:
        return None
    where, title = "channel", ""
    if (thread := await db.get(Thread, block.conversation_id)) is not None:
        where = "thread"
        room = await db.get(Topic, thread.room_id)
        title = room.title if room is not None else ""
    elif (made_in := await db.get(Task, block.conversation_id)) is not None:
        where, title = "task", made_in.title
    elif (room := await db.get(Topic, block.conversation_id)) is not None:
        title = room.title
    return {
        **_said(block),
        "conversation_id": str(block.conversation_id),
        "room_id": str(task.room_id),
        "where": where,
        "title": title,
    }


def materials(blocks: list[Block]) -> list[dict]:
    """What was put on the table in these blocks, each once, in the order it
    came up: a document (``id``, ``title``) or a file (``path``)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []

    def add(kind: str, key: str, item: dict) -> None:
        if (kind, key) in seen:
            return
        seen.add((kind, key))
        out.append({"kind": kind, **item})

    for block in blocks:
        doc = (block.meta or {}).get(DOC_CARD_KEY)
        if isinstance(doc, dict) and doc.get("id"):
            add(
                "document",
                str(doc["id"]),
                {
                    "id": str(doc["id"]),
                    "title": doc.get("title") or "",
                    "by": block.author,
                },
            )
        elif block.kind in (BlockKind.attachment, BlockKind.artifact):
            add("file", block.content, {"path": block.content, "by": block.author})
        elif block.kind == BlockKind.message:
            for match in _FILE_REF.finditer(block.content or ""):
                add(
                    "file", match.group(1), {"path": match.group(1), "by": block.author}
                )
    return out


def materials_text(items: list[dict]) -> str:
    """The list as the task's AI teammate reads it, with what opens each."""
    if not items:
        return ""
    lines = []
    for item in items:
        if item["kind"] == "document":
            title = item["title"] or item["id"]
            lines.append(
                f"- 文档「{title}」（`cheese_doc_get` document: {item['id']}）"
            )
        else:
            lines.append(f"- 文件 `{item['path']}`（{item['by']} 发的）")
    return "讨论里用到的材料：\n" + "\n".join(lines)
