"""这段对话里发过的附件，给频道和私聊的「文件」那一格列 (#3828)。

发进对话的文件跟着对话，不进资料库：它们只在消息里，这里把一个频道或私聊（连同它
的任务和支线）里的附件块收成一份清单，每份一行，新的在前。一份在不在资料库里：从
资料库里挑来附上的（`library/<名字>`）本来就在；发进来的那份，有人按过「保存到资料
库」才在（`records.saved_from`）。
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind
from app.domain.conversation.services import of_rooms
from app.domain.library import records, service

#: 一个频道列多少份。再多的是很久以前的，搜索找得到。
LIMIT = 500


async def attachments(session: AsyncSession, room_id: uuid.UUID) -> list[dict]:
    """这个频道或私聊里的附件，同一份发过几次只算一次，新的在前。"""
    blocks = await session.scalars(
        select(Block)
        .where(
            of_rooms(Block.conversation_id, [room_id]),
            Block.kind == BlockKind.attachment,
        )
        .order_by(Block.created_at.desc())
    )
    kept = await records.saved_from(session, room_id)
    seen: set[str] = set()
    rows: list[dict] = []
    for block in blocks:
        path = block.content
        if not path or path in seen:
            continue
        seen.add(path)
        rows.append(
            {
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "mime": block.mime_type,
                "added_by": block.author,
                "added_at": block.created_at.isoformat(),
                "in_library": service.library_name(path) is not None or path in kept,
            }
        )
        if len(rows) >= LIMIT:
            break
    return rows
