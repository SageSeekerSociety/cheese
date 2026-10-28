"""把一屏任务行交给 HTTP 之前的那一次批量校验与序列化。

一个频道的主线一屏要交几百件任务，逐条 `TaskOut.model_validate(...).model_dump(...)`
是一段不让出事件循环的纯 Python 循环。做法和 `block/adapters.py` 的
`dump_blocks_json` 同一条：按 `chunk` 一块交给 `TypeAdapter`，块与块之间
`await asyncio.sleep(0)`，让别的请求在这段时间里跑。
"""

import asyncio
import enum
from collections.abc import Sequence
from datetime import datetime

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


def _plain(value):
    return value.value if isinstance(value, enum.Enum) else value


def _iso(value: datetime | None) -> str | None:
    """The string pydantic's JSON mode gives a datetime (UTC reads `Z`)."""
    if value is None:
        return None
    text = value.isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def _text(value) -> str | None:
    return None if value is None else str(value)


def task_row_json(task: Task) -> dict:
    """`TaskOut.model_validate(task).model_dump(mode="json")` built straight
    from the row, field for field in `TaskOut`'s order. A list view of N rows
    paid N Pydantic assemblies for it; the shape is pinned against the schema
    by `tests/unit/test_task_row_json.py`."""
    return {
        "id": str(task.id),
        "project_id": str(task.project_id),
        "room_id": str(task.room_id),
        "number": task.number,
        "title": task.title,
        "title_source": _plain(task.title_source),
        "status": _plain(task.status),
        "owner_handle": task.owner_handle,
        "reviewer_handle": task.reviewer_handle,
        "reporter_handle": task.reporter_handle,
        "contributor_handles": list(task.contributor_handles or []),
        "created_by": task.created_by,
        "created_by_assistant": task.created_by_assistant,
        "branch_name": task.branch_name,
        "base_branch": task.base_branch,
        "base_task_id": _text(task.base_task_id),
        "historical_delivery_id": _text(task.historical_delivery_id),
        "pr_number": task.pr_number,
        "pr_url": task.pr_url,
        "delivered_head": task.delivered_head,
        "agent_handle": task.agent_handle,
        "document_id": _text(task.document_id),
        "accepted_by": task.accepted_by,
        "accepted_at": _iso(task.accepted_at),
        "closed_at": _iso(task.closed_at),
        "upgraded_from_block_id": _text(task.upgraded_from_block_id),
        "source_block_id": _text(task.source_block_id),
        "conclusion": task.conclusion,
        "progress_state": _plain(task.progress_state),
        "progress_note": task.progress_note,
        "progress_set_at": _iso(task.progress_set_at),
        "progress_set_by": task.progress_set_by,
        "agent_left_at": _iso(task.agent_left_at),
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "presentation": None,
        "awaits_me": False,
        "needs_my_action": False,
    }
