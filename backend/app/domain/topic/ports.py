"""这个包够不着的那些事，写在这里由调用方递进来。

`topic` 在第 2 层，「这个项目的 AI 队友是谁」只有第 5 层的 `agent_instance` 答得
出来，所以本包把自己要的那几次查问写在这儿，实现留在那个包里
（`app/domain/agent_instance/adapters.py`），谁在门口谁递进去 —— 和
`review/ports.py`、`block/ports.py` 同一条规矩：口子按消费者分开写，实现按提供者
放。

递回来的都是内置类型，没有一个是那个包里的类：本包认不出它的类型，也不该认得。
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class SeatHandles(Protocol):
    """这个项目名下的 AI 队友，按席位 handle 排好：``{席位 handle: 队友的名字}``。

    私聊的角标要把每一席翻回「对面是谁」，而席位 handle 是从实例 id 派生出来的
    （`identity.handles.agent_instance_handle`）—— 那份对应关系只有 `agent_instance`
    拿得到。递回来的是两个内置类型组成的字典。

    键是席位 handle 而不是实例 id：调用方手里拿到的就是名册上那个 handle，翻回
    id 再翻回去是白跑一趟。
    """

    async def __call__(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> dict[str, str]: ...
