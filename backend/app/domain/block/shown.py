"""摆出来给房间看的那一份：写一张 artifact 卡，先不广播。

`cheese show`、房间里复制出来的一份、人自己新建的一份 —— 三件事的终点都是这里：
一张按路径寻址的 artifact 块。**写卡和播报分开**，因为广播用哪个 broker、在哪一次
`commit()` 之前出去，是调用方那一轮的事：同一个房间文件可能从路由进来，也可能从
离线路径进来，把 `db` 和 broker 一起塞进这里就会把「谁在什么时候提交」也搬进来。
值进值出：`project_id` / `room_id` / `path` / `author` / `mime` 都是纯值，不碰
`Place`，也就不会为了一张卡给 block 领域开一条通往 room_task 的边。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut


async def add_shown_block(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    author: str,
    mime: str,
    task_id: uuid.UUID | None = None,
) -> BlockOut:
    """把这个房间的一份文件记进「摆出来的东西」，交出那张卡。

    `mime` 由调用方给：它认得这次是哪种渲染类型，命名与类型的规矩在
    `app.domain.project.room_files` 那边，这里只落一条块。任务里摆出来的带
    `task_id`：它是那个任务的，不是房间的。"""
    block = await BlockRepository(db).add(
        project_id=project_id,
        topic_id=room_id,
        task_id=task_id,
        author=author,
        author_type=AuthorType.participant,
        content=path,
        kind=BlockKind.artifact,
        mime_type=mime,
        refs=[path],
    )
    return BlockOut.model_validate(block)
