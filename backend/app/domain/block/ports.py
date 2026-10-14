"""改一条消息之后，本包够不着的那几步。

一条消息改完有三件事要发生，三件都在第 5 层：跑着的那一轮得知道这条消息变了
（``agent.chat`` 的 ``notify_running_turn``），账本上等着的那几份投递要出队、发给
跑它们的机器（``delivery.agent`` 的 ``dispatch_pending``），任务自己的席位要留一句话
等它下一轮读到（同包的 ``record_task_instruction``）。``block`` 在第 4 层
（`docs/manual/dev/architecture.md` 的层表），上面那些在第 5 层，所以本包只写下自己
要的那几次调用、谁在门口谁递进来 —— 和 `webhook/ports.py` 的 `Poster`、
`integration/ports.py` 的 `RoomFiles` 同一条规矩。

三个口子按**这一次调用**分开写，不攒成一份大的：说给这一轮听、把等着的投递放出去、
给任务留一句话，是三个不同的动作，凑在一起的名字就得说「这个字段只有那一半的调用
点用」。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.delivery_events import DeliveryEvent
from app.domain.task.models import Task


class Notifier(Protocol):
    """说给这个房间里跑着的那一轮听。

    返回值是不透明的：那个实现答的是「跑着的那一轮收下了没有」，收不收得下都要
    接着往下走，本包不认它。"""

    async def notify_running_turn(
        self,
        topic_id: uuid.UUID,
        notice: str,
        *,
        blocks: Sequence[uuid.UUID] = (),
    ) -> object: ...


class Dispatcher(Protocol):
    """把账本上等着的投递放出去。

    不带参数：递进来的那一个已经把房间服务、跑机器和会话工厂包在里面了
    （`app.domain.delivery.agent.dispatch_pending` 要的三样），本包只决定「现在
    放」。返回值不透明：那个实现答的是放出多少份，本包不看。
    """

    async def __call__(self) -> object: ...


class RecordInstruction(Protocol):
    """给任务自己的席位留一句话，等它下一轮读到。"""

    async def __call__(
        self,
        session: AsyncSession,
        event: DeliveryEvent,
        *,
        task: Task,
        content: str,
    ) -> uuid.UUID | None: ...
