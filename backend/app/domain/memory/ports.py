"""搬迁那件事说进房间的那一句 —— ``memory`` 自己说不出口的那一步。

搬迁的每一次开跑和每一次落笔都要在项目总览里说一句（`migration_service.py` 的
`_say_report` / `_say_applied`），而说进房间是 ``app.domain.agent.announce`` 干的：
它在本包上面（`docs/manual/dev/architecture.md`）。所以本包只写下它需要的那一次
调用，谁在门口谁把它递进来 —— 和 `webhook/ports.py` 的 `Poster`、
`integration/ports.py` 的 `RoomFiles` 同一条规矩。

和 `policy/ports.py` 那份不同，这里交回来的行本包认得 —— ``block`` 和 ``memory``
同在第 4 层，名字写得出，就不必当不透明的看。
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block


class Announce(Protocol):
    """把一句话落进一个房间、按 `meta` 投出去。"""

    async def __call__(
        self,
        session: AsyncSession,
        *,
        place_id: uuid.UUID,
        content: str,
        meta: dict,
    ) -> Block | None:
        """Returns what it landed, or ``None`` if the place is gone."""
        ...
