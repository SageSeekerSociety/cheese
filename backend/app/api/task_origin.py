"""Where a task came from: the discussion it was made from, and what was put
on the table in it — documents written or changed there, files sent, files
referred to.

Read from what is already there, never stored: a task made from a message or
its 支线 knows that message (``upgraded_from_block_id``). The task page shows it
as 相关, and the
task's AI teammate is handed the same list with the discussion when it drafts
the document.
"""

import re
from datetime import UTC, datetime

from app.api.routes.topics import BlockRepository
from app.domain.block.documents import DOC_CARD_KEY
from app.domain.block.models import Block, BlockKind
from app.domain.thread.models import Thread

#: How many messages before the one a task comes from count as its discussion.
#: The same window the opening instruction quotes (`task_instructions.py`).
DISCUSSION_BEFORE = 8
#: A file referred to in a message: ``<&path>``.
_FILE_REF = re.compile(r"<&([^>\s]+)>")
_SNIPPET = 200


def _snippet(block: Block | None) -> dict | None:
    if block is None:
        return None
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
