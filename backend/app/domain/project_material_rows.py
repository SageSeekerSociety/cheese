"""The task and accept-card rows this package reads, copied below `room_task`
and `review`.

`project_material` is layer 4 and both of those are layer 4 too, but each of
them reads back into this side one way or another, so importing their models
here is what C3 counts as a cycle. It only ever **reads** their rows — which
work is still going, what it was called, which delivery landed — so the
columns are copied once, the way `block/waits.py` copies `agent_turns` and
`repository/task_rows.py` copies `tasks`: `table(...column(...))`, spelled out
by hand, and only reading goes through here. Writing and deciding stay with
`room_task.services` and `review`, which own the tables.

Copied right the SQL is identical. Copied wrong nothing fails: a renamed column
here still compiles, every unit test stays green, and only an integration test
against a real database finds it.
`tests/unit/test_project_material_rows_match_their_rows.py` is what catches
that locally.
"""

from __future__ import annotations

from sqlalchemy import DateTime, String, Uuid, column, table

#: `tasks`, the columns `project_material` reads. `status` holds the enum
#: member's name (`Enum(native_enum=False)`).
_tasks = table(
    "tasks",
    column("id", Uuid),
    column("project_id", Uuid),
    column("room_id", Uuid),
    column("title", String(300)),
    column("status", String(16)),
    column("document_id", Uuid),
    column("created_at", DateTime(timezone=True)),
    column("updated_at", DateTime(timezone=True)),
)

#: `accept_cards`, the columns it reads. `status` is the same shape.
_cards = table(
    "accept_cards",
    column("task_id", Uuid),
    column("change_subject", String(255)),
    column("deliverable_name", String(255)),
    column("status", String(16)),
    column("decided_at", DateTime(timezone=True)),
)

#: `TaskStatus.open`, spelled out: the enum lives above and this copy cannot
#: import it. Change it there and this goes stale the same way a column name
#: would.
STATUS_OPEN = "open"

#: `AcceptStatus.accepted`, spelled out for the same reason.
STATUS_ACCEPTED = "accepted"
