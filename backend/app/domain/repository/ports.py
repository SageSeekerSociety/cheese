"""本包够不着的那一样东西 —— 谁在门口谁在构造时递进来。

读一条活的检出目录里的文件，要过 ``app.domain.agent.execution``（`forge_files.py`
的 ``live()``）。`repository` 在第 3 层，`agent` 在第 5 层，够不着
（`docs/manual/dev/architecture.md` 的层表），所以本包只写下自己要的那一次调用 ——
和 `memory/ports.py` 的 `ExecutorCall`、`webhook/ports.py` 的 `Poster` 同一条规矩。
"""

from typing import Protocol


class ExecutorCall(Protocol):
    """在一条活的检出目录所在的那台机器上跑一个方法。

    形状是 `app.domain.agent.execution.call` 的形状，收窄到本包要的那三样：地点、
    方法名、参数。``target`` 是会话地点里记着的那一行（`AgentSessionService.place`
    交回来的 ``lease``）—— 本包认不出它的类型，所以这里写 ``dict``。

    递回来的也是 ``dict``：答什么由跑在机器上的那个方法定（``task_fs``），本包只读
    自己知道的那几个键。
    """

    async def __call__(self, target: dict, method: str, params: dict) -> dict: ...
