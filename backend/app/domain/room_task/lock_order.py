"""One lock order for a task and its conversation: the conversation's numbering
first, then the task's row.

Two locks meet whenever a task changes. Every block written into the task's
conversation takes its numbering lock (the advisory lock of `block_numbered()`,
`block.output_effects.hold_numbering`), and a change to the task takes its row.
A message sent in the task takes only the first, and reads the task without
locking it (`agent.turn.store.human`). A change that says so in the
conversation and then writes the row (a teammate given back, the owner handed
over) took them in this order; one that locked or wrote the row and then said
so (a close, a rename, the write-up closers) took them in the other, and the two
kinds aborted each other as deadlocks.

So every transaction takes them in one order, numbering then row, and this
module holds it there:

- `lock_task_for_change` is how a change reads the task it is about to change:
  the numbering, then the row FOR NO KEY UPDATE (what an UPDATE of the row
  takes, so a row that a foreign key only shares, a title's or a delivery's,
  does not wait on it).
- A write of a task row the change did not lock first (an ORM flush, a bulk
  `UPDATE tasks`, a `SELECT … FOR SHARE` / `FOR NO KEY UPDATE` of tasks) takes
  the numbering of each task it names first, here, before the statement runs.
- `SELECT … FOR UPDATE` of tasks is refused: it conflicts with the key share a
  foreign key takes, and a transaction holding that share while it waits for
  the numbering would close a cycle. Lock through `lock_task_for_change`, or
  with ``key_share=True``.
- A task write that names no task by id is refused: whose numbering it needs
  cannot be told.

A transaction that writes several tasks takes their numbering in id order
within a flush; across flushes, in the order it reaches them.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import Delete, Select, Update, event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import operators, visitors
from sqlalchemy.sql.elements import BinaryExpression, BindParameter
from sqlalchemy.sql.util import find_tables

from app.domain.block.output_effects import hold_numbering, hold_numbering_now
from app.domain.room_task.models import Task

_TASKS = "tasks"


async def lock_task_for_change(
    session: AsyncSession, task_id: uuid.UUID
) -> Task | None:
    """``task_id`` read for a change, under the lock order above: its
    conversation's numbering (a task's conversation id is its own), then its
    row FOR NO KEY UPDATE, read again from the database. None when there is no
    such task."""
    await hold_numbering(session, task_id)
    return await session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )


def _names_tasks(table: Any) -> bool:
    return getattr(table, "name", None) == _TASKS


def _task_ids(statement: Any, parameters: Mapping[str, Any]) -> set[uuid.UUID]:
    """The task ids ``statement``'s WHERE names with ``tasks.id = …`` or
    ``tasks.id IN (…)``."""
    where = statement.whereclause
    if where is None:
        return set()
    found: set[uuid.UUID] = set()
    for node in visitors.iterate(where):
        if not isinstance(node, BinaryExpression):
            continue
        column, value = node.left, node.right
        if not (_names_tasks(getattr(column, "table", None)) and column.key == "id"):
            continue
        if not isinstance(value, BindParameter):
            continue
        given = parameters.get(value.key, value.effective_value)
        if node.operator is operators.eq and given is not None:
            found.add(uuid.UUID(str(given)))
        elif node.operator is operators.in_op and given is not None:
            found.update(uuid.UUID(str(one)) for one in given)
    return found


def _locks_tasks(statement: Select) -> bool:
    lock = statement._for_update_arg
    if lock is None or (lock.read and lock.key_share):
        # No lock, or FOR KEY SHARE, which no change to a task's row waits on.
        return False
    targets: Iterable[Any] = (
        lock.of if lock.of is not None else statement.get_final_froms()
    )
    return any(
        _names_tasks(table)
        for target in targets
        for table in find_tables(target, include_joins=True)
    )


@event.listens_for(Session, "do_orm_execute")
def _numbering_before_task_statements(state: ORMExecuteState) -> None:
    statement = state.statement
    if isinstance(statement, (Update, Delete)) and _names_tasks(statement.table):
        pass
    elif isinstance(statement, Select) and _locks_tasks(statement):
        lock = statement._for_update_arg
        assert lock is not None
        if not lock.read and not lock.key_share:
            # i18n-exempt: programming error, raised before any lock is taken
            raise RuntimeError(
                "SELECT … FOR UPDATE of tasks breaks the task lock order: "
                "use lock_task_for_change, or key_share=True"
            )
    else:
        return
    parameters = state.parameters if isinstance(state.parameters, Mapping) else {}
    ids = _task_ids(statement, parameters)
    if not ids:
        # i18n-exempt: programming error, raised before any lock is taken
        raise RuntimeError(
            "a write or lock of tasks must name its tasks by id "
            "(tasks.id = … or tasks.id IN …) to take their numbering first"
        )
    hold_numbering_now(state.session, ids)


@event.listens_for(Session, "before_flush")
def _numbering_before_task_rows(session: Session, _context, _instances) -> None:
    hold_numbering_now(
        session,
        [
            *(
                row.id
                for row in session.dirty
                if isinstance(row, Task) and session.is_modified(row)
            ),
            *(row.id for row in session.deleted if isinstance(row, Task)),
        ],
    )
