"""本包够不着的那几样东西 —— 谁在门口谁在构造时递进来。

关掉一个任务之后要把它在那台机器上的检出目录删掉（`checkouts.py`）。这件事要过
`app.domain.agent.device_hub` 和 `app.domain.agent.resource_cleanup`，而 `room_task`
在第 4 层、`agent` 在第 5 层，够不着（`docs/manual/dev/architecture.md` 的层表）。
所以本包只写下自己要的那一次调用，实现仍留在 `agent` 里 —— 和 `webhook/ports.py`
的 `Poster`、`user/ports.py` 那条同一条规矩：口子按消费者分开写，实现按提供者放。
叫醒任务的 AI 队友（`heard.act`）也是这样：房间里说话的在 `agent`，投递账本在
`delivery`，都在第 5 层。

递进来的是「在这台机器上删掉这几个任务的检出」这一次调用，不是机器本身：分几批、
送哪个脚本、怎么算在线、命令行长什么样，都是机器那边的协议，本包不认也不该认。
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block
from app.domain.delivery_events import DeliveryEvent


class SweepCheckouts(Protocol):
    """在一台机器上删掉某间房那几个任务留在 ``~/.cheese/tasks/`` 下的检出目录。

    参数就是设备上那条命令要的那几样：哪台机器、哪个项目、房间的哪个工作资源，
    和这一批任务的 id。答的是机器上那份脚本的答复：``removed`` 是删掉的目录名，
    ``kept`` 是留下没删的目录名到原因 —— 有进程还开着的检出会留下，下一次再来。
    一台机器上的某一批出错、或者它现在不在线，当场抛出，由调用方决定这一台要不要
    影响别的机器。

    形状是 `app.domain.agent.checkout_sweep.sweep` 的形状。
    """

    async def __call__(
        self, device_id: str, project: str, resource: str, tasks: list[str]
    ) -> dict: ...


class Announce(Protocol):
    """把一句话落进一个房间（或它里面的一个任务）的时间线。

    `heard.act` 用它说给人看的那一行：说话的那段代码在 `agent` 里（第 5 层），本包
    够不着。形状是 `app.domain.agent.announce.announce` 的形状里本包用到的那几样。
    """

    async def __call__(
        self,
        session: AsyncSession,
        *,
        place_id: uuid.UUID,
        content: str,
        meta: dict | None = None,
        task_id: uuid.UUID | None = None,
    ) -> Block | None: ...


class RecordInstruction(Protocol):
    """给任务自己的会话留一条指令，等投递把它交过去（会叫醒它）。

    `heard.act` 用它：投递账本在 `delivery`（第 5 层）。形状是
    `app.domain.delivery.agent.record_task_instruction` 的形状。"""

    async def __call__(
        self,
        session: AsyncSession,
        event: DeliveryEvent,
        *,
        task_id: uuid.UUID,
        content: str,
    ) -> None: ...
