"""block 领域的读入口：房间产出、项目批次事实与反应摘要。

## 为什么这里要有一层，而不是让路由自己去摸 repository

block 领域读得最勤的东西原本直通 `BlockRepository`，项目级的周报集就是其中之一。
HTTP 路由不属于任何领域，它每直接摸一次别人的 repository，就绕过一层这一层
本该守的约束（读什么、读完要不要补字段），而它恰恰是最容易随手摸过去的地方 ——
处理函数手上就有 session（见 `tests/unit/test_domain_import_guard.py` 的开头）。

所以这里不是搬运，是**出口**：项目级的读法有名字，交出去的是 `BlockOut`
（对外的形状）或反应摘要，不是 ORM 行。调用方只认这里的具名查询，
不再认识 `BlockRepository`，也就不会再有人从调用方
那边多摸一个方法出来。

这一层只做折叠，不做判断：顺序（最新在前）在 repository 里已经定了，这里原样
保留；`model_validate` 是唯一的加工。真正的判据（哪一块算周报）是
`BlockKind.weekly`，写在调用点，读的人一眼看得见。
"""

# Public reads leave transaction management to the caller: no explicit
# begin/commit/rollback; SQLAlchemy autobegin and autoflush are unchanged.

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut


async def latest_preview_for_room(
    db: AsyncSession, room_id: uuid.UUID, task_id: uuid.UUID | None = None
) -> BlockOut | None:
    """Return the room's (or its task's) latest artifact through the block read
    boundary."""
    block = await BlockRepository(db).latest_artifact(room_id, task_id=task_id)
    return BlockOut.model_validate(block) if block is not None else None


async def weeklies_for_project(
    db: AsyncSession, project_id: uuid.UUID
) -> list[BlockOut]:
    """这个项目的周报集，最新在前；每条在 `meta` 里带它覆盖的那一段。

    一份周报说的是过去的一段时间，窗口（since/until）是那一行的身份。

    Callers authorize and own this session and its transaction. This read
    does not explicitly begin, commit or roll back.
    """
    blocks = await BlockRepository(db).list_by_kind_for_project(
        project_id, BlockKind.weekly
    )
    return [BlockOut.model_validate(block) for block in blocks]


async def tasks_awaiting_an_answer(
    db: AsyncSession, task_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """这些活里，哪几条停在一个未回答的提问上，各自在等谁 —— 一次查完。

    看板「待回答」那一格问的就是它：这是唯一一种会中断「运行中」的状态，所以它
    和别的批次事实一样，从外面喂进纯函数（见 `room_task/presentation.py`）。
    主语是活、不是房间，所以判据在 `BlockRepository.tasks_awaiting_an_answer`
    里，这里只把路由和那条查询之间的名字固定下来。

    Callers authorize and own this session and its transaction. This read
    does not explicitly begin, commit or roll back.
    """
    return await BlockRepository(db).tasks_awaiting_an_answer(list(task_ids))


async def reaction_summaries_for_blocks(
    db: AsyncSession, block_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict]]:
    """Read ordered reaction counts and authors without exposing block storage.

    The caller owns authorization and the transaction. Native receipts use the
    same session so their effects and these summaries commit together.
    """
    return await BlockRepository(db).reactions_for_blocks(block_ids)
