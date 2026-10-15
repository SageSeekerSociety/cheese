"""在一台机器上删掉某间房那几个已关任务的检出目录。

`room_task.checkouts` 知道该删哪些（它读任务的表、认得房间和机器），但够不着机器：
`room_task` 在第 4 层。所以它把这一次调用声明在自己的 `ports.py` 里，实现留在这里，
构造时把 `sweep` 递进去 —— 和 `agent.announce` 被 `webhook` 递进去是同一条规矩。

删的动作是 `resource_cleanup.py` 的 `tasks` 那一支：那份脚本经 stdin 送到机器上跑，
本模块只管把它送过去、把答复解回来。怎么分批发任务 id 是本模块的事，因为那由机器
的命令行长度决定（见 `ARGV_BUDGET`）。
"""

import json
from pathlib import Path

from app.domain.agent import device_hub, resource_cleanup

# Removing checkouts of a few GB each, after one `lsof` over all of them.
EXEC_TIMEOUT_S = 300.0
# The most characters of task ids one exec names. Windows refuses a command
# line over 32,767 characters ("The filename or extension is too long"), and
# a room on dev has 1,250 closed tasks, about 46,000 characters of ids; so the
# list goes in batches under that, and under cmd.exe's 8,191. Naming a task
# again is harmless (`remove_task_checkouts`), so a batch can be retried alone.
ARGV_BUDGET = 8000


def _batches(tasks: list[str]) -> list[list[str]]:
    batches: list[list[str]] = [[]]
    size = 0
    for task in tasks:
        if batches[-1] and size + len(task) + 1 > ARGV_BUDGET:
            batches.append([])
            size = 0
        batches[-1].append(task)
        size += len(task) + 1
    return batches


async def sweep(device_id: str, project: str, resource: str, tasks: list[str]) -> dict:
    """Remove these tasks' checkouts on `device_id`, a batch at a time.

    A machine that is not online answers nothing removed — an offline machine
    is swept when it connects (`room_task.checkouts`, module docstring). One
    that goes away mid-run raises: the caller has other machines to get to,
    and this machine's next run names the whole list again.
    """
    hub = device_hub.device_hub  # 每一次都取名字：测试替的就是这个名字
    if not hub.is_online(device_id):
        return {"removed": [], "kept": {}}
    script = Path(resource_cleanup.__file__).read_text()
    removed: list[str] = []
    kept: dict[str, str] = {}
    for batch in _batches(tasks):
        result = await hub.exec(
            device_id,
            ["python3", "-", "tasks", project, resource, "-", "-", *batch],
            stdin=script,
            timeout=EXEC_TIMEOUT_S,
        )
        if result.get("exit") != 0 or result.get("truncated"):
            raise RuntimeError(
                str(result.get("stderr") or "no answer from the machine")[-1500:]
            )
        outcome = json.loads(result["stdout"])
        removed += outcome["removed"]
        kept.update(outcome["kept"])
    return {"removed": removed, "kept": kept}
