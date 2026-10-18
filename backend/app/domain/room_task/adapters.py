"""把一屏任务行交给 HTTP 之前的那一次批量校验与序列化。

一个频道的主线一屏要交几百件任务，逐条 `TaskOut.model_validate(...).model_dump(...)`
是一段不让出事件循环的纯 Python 循环。做法和 `block/adapters.py` 的
`dump_blocks_json` 同一条：按 `chunk` 一块交给 `TypeAdapter`，块与块之间
`await asyncio.sleep(0)`，让别的请求在这段时间里跑。
"""

import asyncio
from collections.abc import Sequence

from pydantic import TypeAdapter

from app.domain.room_task.models import Task
from app.domain.room_task.schemas import TaskOut

_TASK_LIST_ADAPTER = TypeAdapter(list[TaskOut])


async def dump_task_rows_json(tasks: Sequence[Task], chunk: int = 500) -> list[dict]:
    """Validate and dump task rows to JSON-mode dicts in chunks, yielding to the
    event loop (`await asyncio.sleep(0)`) between chunks so a multi-thousand-row
    room list does not monopolize the loop.

    The result is what `TaskOut.model_validate(task).model_dump(mode="json")`
    gives per row, in the same order."""
    step = max(chunk, 1)
    items = tasks if isinstance(tasks, (list, tuple)) else list(tasks)
    if not items:
        return []
    out: list[dict] = []
    for i in range(0, len(items), step):
        if i > 0:
            await asyncio.sleep(0)
        validated = _TASK_LIST_ADAPTER.validate_python(items[i : i + step])
        out.extend(_TASK_LIST_ADAPTER.dump_python(validated, mode="json"))
    return out
