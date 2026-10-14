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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.addressing import NAMES_NOBODY, Event
from app.domain.block.models import Block
from app.domain.delivery_events import DeliveryEvent


class Chat(Protocol):
    """跑这个房间的那个服务，本包只认得它身上两样。

    形状是 `app.domain.agent.chat.ChatService` 的形状。`session_factory` 是本包自己
    开事务要的那一样；`work_runner` 本包不碰，只跟着 `chat` 一起往下递给合并路径上
    那几层。

    两个都写成只读属性，而不是可写的字段：`ChatService` 上这两个本来就是
    ``@property``，而只读成员比的是返回类型（可以更窄），于是 `work_runner` 真实的
    那个类型（`agent.session_turn_events` 里的 `SessionTurnEvents`）落在 `object`
    里，本包不必为了写它再往 `agent` 伸一条边。
    """

    @property
    def session_factory(self) -> async_sessionmaker: ...

    @property
    def work_runner(self) -> object: ...


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
    不是席位那一行：`AgentInstance` 是第 5 层的模型，本包认不出它。"""

    async def __call__(
        self, session: AsyncSession, project_id: uuid.UUID, seat: str
    ) -> uuid.UUID | None: ...


class TaskTeammate(Protocol):
    """这个任务现在的 AI 队友是哪一个席位：``(实例 id, 它的 handle)``。

    一个任务由它项目里的某一个 AI 队友来做，那一个是谁，只有 `agent_instance`
    答得出来（第 5 层），所以本包把这一次查问写在这里。递回来的是两个内置类型，
    不是那个包里的 ``ResolvedAgent``：本包认不出它的类型。

    实例 id 是署这个任务的话时用的那一个（本包用
    `identity.handles.agent_instance_handle` 把它写成席位 handle），handle 是
    `room_task.closing.unfinished` 挑「这个队友自己干的活」时用的那一个
    （`agent_turns.agent_handle` 记的就是这个值）—— 两个值都是同一次查问的答案，
    所以一个口子答两个，不拆成两个再查一遍。

    项目或房间不在了是 ``None``（那个任务本来就无处落脚，也不该有队友）。"""

    async def __call__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        agent_handle: str | None,
    ) -> tuple[uuid.UUID, str] | None: ...


class DispatchPending(Protocol):
    """把投递账本上等着的那些放出去。

    不带参数：递进来的那一个已经把房间服务、跑机器和会话工厂包在里面了
    （`app.domain.delivery.agent.dispatch_pending` 要的三样），本包只决定「现在放」。
    返回值不透明：那个实现答的是放出多少份。"""

    async def __call__(self) -> object: ...
