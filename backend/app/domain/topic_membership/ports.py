"""这个包够不着的那些事，写在这里由调用方递进来。

`topic_membership` 在第 2 层，「这一席落在哪个项目上」只有第 5 层的 `agent_instance`
答得出来，所以本包把这一次查问写在这儿，实现留在那个包里
（`app/domain/agent_instance/adapters.py`），谁在门口谁递进去 —— 和
`topic/ports.py`、`review/ports.py` 同一条规矩：口子按消费者分开写，实现按提供者
放。

递回来的都是内置类型，没有一个是那个包里的类：本包认不出它的类型，也不该认得。
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class SeatProject(Protocol):
    """``handle`` 这个席位落在哪个项目上，不是任何一个队友的席位就是 ``None``。

    落座这一处要挡住「别的项目的队友坐进这间房」：队友的席位是从实例派生的，而实例
    在**它自己**的项目里认着一份记忆池 —— 别处的队友坐进来，本项目的谁也叫不动它，
    它那块令牌却会以这间房的默认身份说话。席位落在哪个项目只有 `agent_instance`
    查得出，本包拿它跟房间的项目比一比。

    「谁也查不到」和「查到别的项目」是两回事：前者是连接器绑来的账号，它按名字落
    座，不该被这一问挡住。
    """

    async def __call__(
        self, session: AsyncSession, handle: str
    ) -> uuid.UUID | None: ...
