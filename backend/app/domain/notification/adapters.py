"""把一屏收件箱的行交给 HTTP 之前的那一次分块序列化。

和 `room_task/adapters.py` 的 `dump_task_rows_json`、`block/adapters.py` 的
`dump_blocks_json` 同一条做法：按 `chunk` 一块校验、一块 dump，块与块之间
`await asyncio.sleep(0)`，让出事件循环。

通知行的 HTTP 字段名和表上的列名不一样（`kind`/`target_handle`/`payload` 对
`type`/`recipient_handle`/`metadata`），翻译只在 `schemas.NotificationOut.from_row`
那一处发生，所以这里逐行用它，再把整块一次 dump 成 JSON。
"""

import asyncio
from collections.abc import Sequence

from pydantic import TypeAdapter

from app.domain.notification.schemas import NotificationOut, NotificationRow

_NOTIFICATION_LIST_ADAPTER = TypeAdapter(list[NotificationOut])


async def dump_notification_rows_json(
    rows: Sequence[NotificationRow], chunk: int = 500
) -> list[dict]:
    """Validate and dump notification rows to JSON-mode dicts in chunks, yielding
    to the event loop (`await asyncio.sleep(0)`) between chunks so a full inbox
    does not monopolize the loop.

    The result is what `NotificationOut.from_row(row).model_dump(mode="json")`
    gives per row, in the same order."""
    step = max(chunk, 1)
    items = rows if isinstance(rows, (list, tuple)) else list(rows)
    if not items:
        return []
    out: list[dict] = []
    for i in range(0, len(items), step):
        if i > 0:
            await asyncio.sleep(0)
        validated = [NotificationOut.from_row(row) for row in items[i : i + step]]
        out.extend(_NOTIFICATION_LIST_ADAPTER.dump_python(validated, mode="json"))
    return out
