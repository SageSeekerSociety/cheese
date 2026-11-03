"""本包为在它下面的层声明的口子实现的那些东西。

`notification` 在第 3 层、`topic` 在第 2 层，都够不着第 4 层的 `block`
（`docs/manual/dev/architecture.md` 的层表），所以它们在自己的 `ports.py` 里写下
**自己要的那一次写**（`DecisionLine`、「平台说的一句话」`PlatformLine`），实现留在
这里，谁在门口谁递进去 —— 和 `task/adapters.py`、`thread/adapters.py`、
`usage/adapters.py` 同一条规矩：口子按消费者分开写，实现按提供者放。

`DecisionLine` 把落下的那一行收成可以直接投出去的形状（`BlockOut`）。
本模块不 commit —— 那一笔和调用方自己那几行在同一个事务里，由调用方收尾。
"""

import asyncio
import uuid
from collections.abc import Sequence

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import BY_ASSISTANT_META_KEY, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.event_about import EventAbout, landing
from app.domain.event_author import AuthorType

_BLOCK_LIST_ADAPTER = TypeAdapter(list[BlockOut])


async def dump_blocks_json(blocks: Sequence[Block], chunk: int = 500) -> list[dict]:
    """Validate and dump a sequence of blocks to JSON-mode dicts in chunks,
    yielding to the event loop (`await asyncio.sleep(0)`) between chunks so a
    multi-thousand-block page does not monopolize the loop."""
    step = max(chunk, 1)
    items = blocks if isinstance(blocks, (list, tuple)) else list(blocks)
    if not items:
        return []
    out: list[dict] = []
    for i in range(0, len(items), step):
        if i > 0:
            await asyncio.sleep(0)
        validated = _BLOCK_LIST_ADAPTER.validate_python(items[i : i + step])
        out.extend(_BLOCK_LIST_ADAPTER.dump_python(validated, mode="json"))
    return out


class DecisionLine:
    """`app.domain.notification.ports.DecisionLine`：把「拍板了」这一句落进那条对话。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __call__(
        self,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        author: str,
        content: str,
        by_assistant: bool = False,
    ) -> dict:
        block = await BlockRepository(self._session).add(
            project_id=project_id,
            conversation_id=conversation_id,
            author=author,
            author_type=AuthorType.participant,
            content=content,
            kind=BlockKind.message,
            meta={BY_ASSISTANT_META_KEY: True} if by_assistant else None,
        )
        return BlockOut.model_validate(block).model_dump(mode="json")


async def platform_line(
    session: AsyncSession,
    *,
    about: EventAbout,
    project_id: uuid.UUID,
    author: str,
    content: str,
    room_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    thread_id: uuid.UUID | None = None,
    meta: dict | None = None,
) -> None:
    """`app.domain.topic.ports.PlatformLine`：平台在某条对话上说一句话。

    房间归档、取消归档、一间房收工时在每条活自己的支线上说的那句，都是这一件事 ——
    署名是平台、`kind=event`、`meta` 上记着「这是平台说的」。「说什么」由调用方决定
    （那句话和它关于什么，只有调用方知道），落点由 `landing()` 那张封闭表算：这里
    只把四档里缺 id 的那几种当场抛出来，不替调用点挑一个房间。

    ``landing()`` 的三种 id 原样收下：`topic` 手里同时有房间、卡和支线三种 id，而
    四档里哪几样是必给的，正是那张表要说清的事。

    ``meta`` 加在「这是平台说的」旁边：说给任务的 AI 队友、房间里不显示的那一句
    （`room_task.heard.inform`）就是这么落的。
    """
    landed = landing(
        about,
        project_id=project_id,
        room_id=room_id,
        task_id=task_id,
        thread_id=thread_id,
    )
    await BlockRepository(session).add(
        project_id=landed.project_id,
        conversation_id=landed.conversation_id,
        author=author,
        author_type=AuthorType.platform,
        content=content,
        kind=BlockKind.event,
        meta={**(meta or {}), "platform": True},
    )
