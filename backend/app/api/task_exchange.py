"""What passed between a channel's main line and a task, as rows of its 现场.

The main line tells a task what a member added or the answer to its question,
and the task reports back a question or that it cannot go on. Neither is said
in any conversation: each is kept as a delivery (`delivery.agent.exchange_of`).
Everyone who can see the task sees them in its 现场, in order with the rest of
what its session did — each as its own kind of row (`meta.exchange`), read
straight from those deliveries.

A row is shaped like the 现场's other platform rows (`run_record.service.
as_payload`): `kind=event`, written by the platform, with its own cursor so a
page boundary can fall on it. Its author is whoever said it — the main line's
AI teammate for what it told, the task's for what it reported — and its
content is their words as they said them (`said`), not the framing the other
side reads them in.

Rows kept before the speaker and the words were recorded on the delivery
(the day #5287 shipped) show that framing and no speaker: the deliveries are
the only copy of what was said, and the words cannot be recovered from it.
"""

import uuid
from datetime import datetime

from app.domain.delivery.agent import TASK_TOLD, exchange_of
from app.domain.session_record.service import Key, encode_cursor

#: The middle field of an exchange row's cursor: which family wrote it.
FAMILY = "exchange"


async def exchange_rows(
    db,
    task_id: uuid.UUID,
    *,
    before: Key | None = None,
    after: Key | None = None,
    author: str | None = None,
) -> list[dict]:
    """The exchange of ``task_id`` as 现场 rows, oldest first: those before
    the cursor ``before`` or after ``after`` when given, and with ``author``,
    only what that teammate said or was told."""
    rows = []
    for delivery in await exchange_of(db, task_id):
        payload = delivery.payload or {}
        sender = payload.get("from") or "system"
        if author is not None and author not in (sender, delivery.recipient_handle):
            continue
        key = (delivery.recorded_at, FAMILY, sender, delivery.id.int)
        if before is not None and key >= before:
            continue
        if after is not None and key <= after:
            continue
        rows.append(_row(delivery, payload, sender, key, task_id))
    return rows


def _row(delivery, payload: dict, sender: str, key, task_id: uuid.UUID) -> dict:
    at: datetime = delivery.recorded_at
    return {
        "id": str(delivery.id),
        "conversation_id": str(task_id),
        "kind": "event",
        "author_type": "platform",
        "author": sender,
        "content": payload.get("said") or payload.get("content") or "",
        "reply_to": None,
        "refs": [],
        "turn_id": None,
        "meta": {
            # `told`: the main line to the task; `report`: the task to it.
            "exchange": "told" if payload.get("eventType") == TASK_TOLD else "report",
            "to": delivery.recipient_handle,
            **({"asker": payload["asker"]} if payload.get("asker") else {}),
            **({"kind": payload["kind"]} if payload.get("kind") else {}),
            "cursor": encode_cursor(key),
        },
        "created_at": at.isoformat(),
        "updated_at": None,
    }
