"""The task row, read from this package's side of the boundary.

`room_task` imports `block` (it reads blocks to close a room's tasks and to name
them), so `block` may not import it back — that pair is a C3 cycle. Two readers
here need a task row anyway: a document asks which task it is the living
document of, and an edit asks which room the task it edits sits in.

So the columns are copied once, the way `document/address.py`,
`topic/repositories/statements.py` and `repository/task_rows.py` copy theirs:
`table(...column(...))`, spelled out by hand, and **only reading goes through
here**. Writing and deciding stay with `room_task.services`, which owns the
table.

Copied right the SQL is identical. Copied wrong nothing fails: the real table
renames a column, the `WHERE` here still names the old one, every unit test
stays green, and only an integration test against a real database finds it.
`tests/unit/test_block_task_rows_match_their_rows.py` is what catches that
locally.
"""

from __future__ import annotations

from sqlalchemy import String, Uuid, column, table

#: The columns these two readers take. `documents.py` reads all five; `editing.py`
#: reads `room_id` by `id`.
_tasks = table(
    "tasks",
    column("id", Uuid),
    column("room_id", Uuid),
    column("document_id", Uuid),
    column("title", String),
    column("title_source", String),
)
