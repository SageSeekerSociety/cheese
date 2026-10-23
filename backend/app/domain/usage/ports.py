"""本包够不着的那一件读：某个会话里每一轮是什么时候开始的。

`usage` 在第 3 层、`block` 在第 4 层（`docs/manual/dev/architecture.md` 的层表），
所以那几张表本包自己读不了。代理日志一行记着项目、会话和四个 token 桶，唯独没有
work id；要把一次调用挂到当时跑着的那一轮上，只能问 `block` 那个会话的各轮是从什么
时候开始的（`app/domain/block/queries.py` 的 `turn_starts`）。

形状就是那个函数：会话 id 进来，一串 ``(那一轮的开始时刻, 它认的 work id)`` 出去，
按时刻从早到晚，全是内置类型 —— 所以调用方把它原样递进来即可，不需要适配，和
`project/ports.py` 的 `SeedOverview`、``preview.office.prewarm`` 一样。谁在门口谁递
（`app/periodic_jobs.py` 传到 `app/domain/usage/periodic.py`），本包只声明自己要的
那一次读。
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class TurnStarts(Protocol):
    """某个会话里每一轮开始于什么时刻，按时刻从早到晚。

    答的是**那一个会话自己的**轮次：一个任务的轮次挂在任务自己的会话下，本包按它
    拿到的那个 id 问，够不到房间的那些。
    """

    async def __call__(
        self, session: AsyncSession, conversation_id: uuid.UUID
    ) -> Sequence[tuple[datetime, uuid.UUID]]: ...
