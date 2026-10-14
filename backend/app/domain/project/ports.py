"""保存一份房间文件之后要说进房间的那一句 —— 本包说不出口的那一步。

「保存到资料库」是一个动作，按了才算（`room_files.py`），按了之后房间里要留一句
话。说进房间是 ``app.domain.agent.announce`` 干的：它在第 5 层，本包在第 2 层
（`docs/manual/dev/architecture.md`）。所以本包只写下它需要的那一次调用，谁在门口
谁把它递进来 —— 和 `webhook/ports.py` 的 `Poster`、`integration/ports.py` 的
`RoomFiles` 同一条规矩。

返回值是不透明的：``announce()`` 交回它写下的那个 ``Block``，而 ``block`` 也在本包
上面。本包这里不关心落下了哪一行，只声明**可能什么都没落**（房间没了）。
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class Announce(Protocol):
    """把一句话落进一个房间、按 `meta` 投出去。"""

    async def __call__(
        self,
        session: AsyncSession,
        *,
        place_id: uuid.UUID,
        task_id: uuid.UUID | None,
        content: str,
        meta: dict | None,
    ) -> object | None:
        """Returns what it landed, or ``None`` if the place is gone."""
        ...
