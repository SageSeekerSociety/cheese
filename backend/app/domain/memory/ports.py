"""本包够不着的那两样东西 —— 谁在门口谁在构造时递进来。

一处是说进房间：搬迁的每一次开跑和每一次落笔都要在项目总览里说一句
（`migration_service.py` 的 `_say_report` / `_say_applied`），而说进房间是
``app.domain.agent.announce`` 干的。另一处是在机器上跑一次检索：写记忆之前要对着
这条活的检出目录问一句「这件事 repo 里是不是已经写着了」（`redundant.py`），而够到
那台机器要过 ``app.domain.agent.execution``。两样都在本包上面
（`docs/manual/dev/architecture.md` 的层表），所以本包只写下自己要的那两次调用 ——
和 `webhook/ports.py` 的 `Poster`、`integration/ports.py` 的 `RoomFiles` 同一条规矩。

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


class ExecutorCall(Protocol):
    """在一条活的检出目录所在的那台机器上跑一个方法。

    形状是 `app.domain.agent.execution.call` 的形状，收窄到本包要的那三样：地点、
    方法名、参数。``target`` 是会话地点里记着的那一行（`AgentSessionService.place`
    交回来的 ``lease``）—— 本包认不出它的类型，所以这里写 ``dict``。

    递回来的也是 ``dict``：答什么由跑在机器上的那个方法定（``repo_search``），本包
    只读自己知道的那几个键，读不到就当这一次没查成。
    """

    async def __call__(self, target: dict, method: str, params: dict) -> dict: ...
