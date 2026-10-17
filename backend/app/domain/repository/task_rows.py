"""A task's row, read from this package instead of from `room_task`.

`repository` is layer 3 and `room_task` is layer 4, so this package cannot
reach that domain's model — yet it reads the very rows that model writes: is
this piece of work still going, what are its branches, what did it last land,
whose work is it. Reading them the other way (`from app.domain.room_task...`)
is an upward import, and those are what the C5 contract counts down.

So the columns this package reads are copied here once, the same way
`topic/repositories/statements.py` copies `tasks`, `threads` and `blocks`:
`table(...column(...))`, spelled out by hand, and **only reading goes through
here**. Writing and deciding stay with the service that owns the table
(`room_task.services`) — this file grows a reader, never a second author.

Copied right, the SQL is identical. Copied wrong, nothing fails: the real table
renames a column, the `WHERE` here still names the old one, every unit test
stays green, and only an integration test against a real database finds it.
`tests/unit/test_repository_task_rows_match_their_rows.py` is what catches that
locally.

`status` holds the enum member's name (`Enum(native_enum=False)`), and
`TaskStatus` is in the layer above, so the one code this package judges is
written down below. Change it there and this goes stale the same way a column
name would.

A module rather than a function on each caller: two readers asking the same
question must ask it the same way, and the guard test can stand in for the
table once, in one place.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Uuid, column, select, table

#: `TaskStatus.open`, spelled out: see the module docstring.
STATUS_OPEN = "open"

# 只声明这一包读到的列：`forge_files.py` 读 id / project_id / room_id / status /
# branch_name / base_branch / pr_number / delivered_head / closing_since；
# `identity.py` 读 id / title / owner_handle / reporter_handle / created_by /
# author_handle / contributor_handles，批量那条还要 created_at 排序。
# `tests/unit/test_repository_task_rows_match_their_rows.py` 把这份清单钉住了。
_tasks = table(
    "tasks",
    column("id", Uuid),
    column("project_id", Uuid),
    column("room_id", Uuid),
    column("status", String(16)),
    column("title", String(300)),
    column("branch_name", String(255)),
    column("base_branch", String(255)),
    column("pr_number", Integer),
    column("delivered_head", String(64)),
    column("closing_since", DateTime(timezone=True)),
    column("owner_handle", String(64)),
    column("reporter_handle", String(64)),
    column("created_by", String(64)),
    column("author_handle", String(64)),
    column("contributor_handles", JSON),
    column("created_at", DateTime(timezone=True)),
)


def is_open(task: Any) -> bool:
    """Whether this piece of work is still going. Reads `status` alone."""
    return task.status == STATUS_OPEN


async def one(session: Any, task_id: uuid.UUID):
    """This task's row, or None."""
    return (await session.execute(select(_tasks).where(_tasks.c.id == task_id))).first()


async def many(session: Any, task_ids: list[uuid.UUID]) -> list[Any]:
    """These tasks' rows in table order (`created_at`, `id`), absent ones left out.

    An empty list builds no statement: the caller that reads a card's declared
    ids already answers "no ids, no work" and must not pay for a round trip.
    """
    if not task_ids:
        return []
    return list(
        (
            await session.execute(
                select(_tasks)
                .where(_tasks.c.id.in_(task_ids))
                .order_by(_tasks.c.created_at, _tasks.c.id)
            )
        ).all()
    )
