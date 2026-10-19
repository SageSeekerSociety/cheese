"""本包够不着的那一样东西 —— 谁在门口谁在构造时递进来。

关掉一个任务之后要把它在那台机器上的检出目录删掉（`checkouts.py`）。这件事要过
`app.domain.agent.device_hub` 和 `app.domain.agent.resource_cleanup`，而 `room_task`
在第 4 层、`agent` 在第 5 层，够不着（`docs/manual/dev/architecture.md` 的层表）。
所以本包只写下自己要的那一次调用，实现仍留在 `agent` 里 —— 和 `webhook/ports.py`
的 `Poster`、`user/ports.py` 那条同一条规矩：口子按消费者分开写，实现按提供者放。

递进来的是「在这台机器上删掉这几个任务的检出」这一次调用，不是机器本身：分几批、
送哪个脚本、怎么算在线、命令行长什么样，都是机器那边的协议，本包不认也不该认。
"""

from typing import Protocol


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
