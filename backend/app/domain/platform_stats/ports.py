"""本包够不着的那两样东西 —— 由 api 在构造看板服务时递进来。

本包在第 7 层，`agent` 在第 5 层，按理够得着（`docs/manual/dev/architecture.md`
的层表），可两个包落在同一个强连通分量里（C3），import 一次就冻一条边、还得再
还一次。所以这里只写下要问的那几个问题，实现留在 `agent` 里 ——
和 `webhook/ports.py` 的 `Poster`、`room_task/ports.py` 的 `SweepCheckouts` 同一条
规矩：口子按消费者分开写，实现按提供者放。

递进来的是**答案**，不是表：本包不认 `agent_turns` 和 `dispatch_log` 的列，也不该认。
"""

from datetime import datetime
from typing import Protocol


class PreviewConnections(Protocol):
    """本进程里挂着的预览 helper 数（`agent.preview_hub.PreviewHub.attached`）。

    界面上的「在线」在这里是**进程内存**里的东西，库里没有那一列 —— 这也是为什么
    它答不了「全平台」：重启即清零，多进程各算各的。
    """

    def attached(self) -> int: ...


class TurnCounts(Protocol):
    """轮次账与工具调用账上的三个数（`agent.turn_counts.TurnCounts`）。

    两个窗口数收 ``since``/``until``，一个存量数不收 —— 收不收是这两张表各自的
    口径，不是调用方的选择。
    """

    async def credits_refused(self, *, since: datetime, until: datetime) -> int: ...

    async def prompt_undelivered(self, *, since: datetime, until: datetime) -> int: ...

    async def unsettled_dispatches(self) -> int: ...
