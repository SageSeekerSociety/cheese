"""review 够不着的那几样东西，由上层在构造时递进来。

`app.domain.review` 在第 4 层，`agent`、`agent_instance`、`delivery` 在第 5 层
（`docs/manual/dev/architecture.md` 的层表），所以这个包够不到那三个包里做事的那些
函数。它把**自己要的那一次调用**写在这里，实现留在上面，
调用点递进来 —— 和 `webhook/ports.py` 的 `Poster`、`integration/ports.py` 的
`RoomFiles`、`delivery_events.py` 的 `Deliver` 是同一条规矩：谁调用谁给。

声明按**消费者**分开写而不是攒一份大的：每个包里要的东西不一样，凑在一起的名字
就得说「这个字段只有那一半的调用点用」。
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.addressing import NAMES_NOBODY, Event
from app.domain.block.models import Block
from app.domain.delivery_events import DeliveryEvent


class Announce(Protocol):
    """把一句话落进一个房间的时间线，并投给这句话点到的人。"""

    async def __call__(
        self,
        session: AsyncSession,
        *,
        place_id: uuid.UUID,
        content: str,
        meta: dict | None = None,
        author: str = "system",
        turn_id: uuid.UUID | None = None,
        points_at: Event = NAMES_NOBODY,
        event_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        about_card: uuid.UUID | None = None,
        published_by_caller: bool = False,
    ) -> Block | None:
        """落下的那一行；房间已经不在了返回 ``None``。

        这一个的形状就是 `app.domain.agent.announce.announce` 的形状：参数名、默认
        值、返回的都是 ``Block`` —— 卡和这句话住同一层，所以这里的返回不必像
        `Poster` 那样写成 `object`。
        """
        ...


class RecordInstruction(Protocol):
    """给任务自己的席位留一句话，等它下一轮读到。

    形状是 `app.domain.delivery.agent.record_task_instruction` 的形状；返回的是
    记下的那一行投递的 id（一个任务同时只有一个席位，所以这一行也认得出是谁），
    没记下就是 ``None`` —— 同一次事件里要投给别的席位时按它去重。

    收任务的 id 而不是那一行：那一行是第 4 层 `room_task` 的模型，被递进来的
    那几个函数要的是它里面的四个字段，读表的事留在投递包自己做。"""

    async def __call__(
        self,
        session: AsyncSession,
        event: DeliveryEvent,
        *,
        task_id: uuid.UUID,
        content: str,
    ) -> uuid.UUID | None: ...


class RecordAgent(Protocol):
    """把一条事件记进某个 agent 席位那本报账，不开工，只投递。

    形状是 `app.domain.delivery.agent.record_agent` 的形状。返回值不透明：那个实现
    落下的行本包不看，记完就完。"""

    async def __call__(
        self,
        session: AsyncSession,
        event: DeliveryEvent,
        *,
        conversation_id: uuid.UUID,
        instance_id: uuid.UUID,
        content: str,
    ) -> object: ...


class SeatInstanceId(Protocol):
    """这个项目里某个席位的实例 id；没有这个席位就是 ``None``。

    形状是 `app.domain.delivery.agent.seat_instance_id` 的形状。递回来的是 id 而
    不是席位那一行：`AgentInstance` 是第 3 层的模型，本包认不出它。"""

    async def __call__(
        self, session: AsyncSession, project_id: uuid.UUID, seat: str
    ) -> uuid.UUID | None: ...


class DispatchPending(Protocol):
    """把投递账本上等着的那些放出去。

    不带参数：递进来的那一个已经把房间服务、跑机器和会话工厂包在里面了
    （`app.domain.delivery.agent.dispatch_pending` 要的三样），本包只决定「现在放」。
    返回值不透明：那个实现答的是放出多少份。"""

    async def __call__(self) -> object: ...
