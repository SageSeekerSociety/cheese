"""摆出来给房间看的那一份：写一张 artifact 卡，先不广播。

`cheese show`、房间里复制出来的一份、人自己新建的一份 —— 三件事的终点都是这里：
一张按路径寻址的 artifact 块。**写卡和播报分开**，因为广播用哪个 broker、在哪一次
`commit()` 之前出去，是调用方那一轮的事：同一个房间文件可能从路由进来，也可能从
离线路径进来，把 `db` 和 broker 一起塞进这里就会把「谁在什么时候提交」也搬进来。
值进值出：`project_id` / `room_id` / `path` / `author` / `mime` 都是纯值，不碰
`Place`，也就不会为了一张卡给 block 领域开一条通往 room_task 的边。
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.event_author import AuthorType


async def add_shown_block(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    path: str,
    author: str,
    mime: str,
    meta: dict | None = None,
) -> BlockOut:
    """把这个房间的一份文件记进「摆出来的东西」，交出那张卡。

    `mime` 由调用方给：它认得这次是哪种渲染类型，命名与类型的规矩在
    `app.domain.project.room_files` 那边，这里只落一条块。块落在 `conversation_id`
    那段对话里：任务里摆出来的是那个任务的，不是房间的。"""
    block = await BlockRepository(db).add(
        project_id=project_id,
        conversation_id=conversation_id,
        author=author,
        author_type=AuthorType.participant,
        content=path,
        kind=BlockKind.artifact,
        mime_type=mime,
        meta=meta,
        refs=[path],
    )
    return BlockOut.model_validate(block)


#: How long a second `cheese show` of the same, unchanged file counts as a repeat
#: of the card already in the conversation rather than a new showing. A file
#: shown twice seconds apart (once from the task directory, once from the room's)
#: left two identical cards; an hour later it is a deliberate "here it is again".
SHOWN_AGAIN_WINDOW = timedelta(minutes=5)


async def repeat_of_latest_shown(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    path: str,
    mime: str,
    file_saved_at: datetime,
) -> BlockOut | None:
    """The card this showing would duplicate, or None when it needs a card of its own.

    It duplicates the conversation's newest card when that card shows the same
    path as the same kind, went up within :data:`SHOWN_AGAIN_WINDOW`, and the file
    has not been saved since (``file_saved_at``, when the page or a file it loads
    was last saved, is not after the card). A changed file is a new version and
    gets a new card."""
    latest = await BlockRepository(db).latest_artifact(conversation_id)
    if (
        latest is None
        or latest.content != path
        or latest.mime_type != mime
        or file_saved_at > latest.created_at
        or datetime.now(UTC) - latest.created_at > SHOWN_AGAIN_WINDOW
    ):
        return None
    return BlockOut.model_validate(latest)
