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
