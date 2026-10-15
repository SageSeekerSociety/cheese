"""拦住这一次调用之后，要把这条提议说进房间 —— 只有上面答得了的那一步。

闸门（`gate.py`）是纯的：一条调用、一份策略、一个发起人，算出来该拦还是该放。拦住
的那一条要落成房间里的一条事件、投给要点头的那个人，这一步是 `proposals.py` 的
`propose` 做的，而真正落事件的是 ``app.domain.agent.announce`` —— 它在第 5 层，
本包在第 2 层（`docs/manual/dev/architecture.md`）。所以本包只写下它需要的那一次
调用，谁调用谁在门口把它递进来 —— 和 `webhook/ports.py` 的 `Poster`、
`integration/ports.py` 的 `RoomFiles` 同一条规矩。

返回值是不透明的：``announce()`` 交回它写下的那个 ``Block``，而 ``block`` 也在本包
上面。这里只声明**可能什么都没落**（房间没了），拿到之后要什么，调用方自己认。
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.addressing import Event


class Announce(Protocol):
    """把一句话落进一个房间、按 `meta` 投出去。"""

    async def __call__(
        self,
        session: AsyncSession,
        *,
        place_id: uuid.UUID,
        content: str,
        meta: dict,
        points_at: Event,
        event_id: uuid.UUID,
    ) -> object | None:
        """Returns what it landed, or ``None`` if the place is gone."""
        ...
