"""A task and its conversation, in the one numbering order (`conversation.numbering`).

A change to a task takes its conversation's numbering lock and then its row.
The numbering comes first, in the batch of every conversation the transaction
will write into (`conversation.numbering`); the row after. A message sent in a task
takes only the numbering, and reads the task without locking it
(`agent.turn.store.human`).

- `lock_task_for_change` / `lock_tasks_for_change` is how a change reads the
  tasks it is about to change: their numbering, with each one's room and the
  支线 it came from (where a change says how it went) and every other
  conversation it will say something in (``also``), in one batch; then each row FOR NO KEY
  UPDATE, in id order (what an UPDATE takes, so a row a foreign key only
  shares does not wait on it).
- A task row written without them (an ORM flush, a bulk `UPDATE tasks`, a
  `SELECT … FOR SHARE` / `FOR NO KEY UPDATE`) takes its numbering here before
  the statement runs, under the same batch rule.
- `SELECT … FOR UPDATE` of tasks is refused: it conflicts with the key share a
  foreign key takes. A task write that names no task by id is refused: whose
  numbering it needs cannot be told.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import Delete, Select, Update, Uuid, column, event, select, table
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import operators, visitors
from sqlalchemy.sql.elements import BinaryExpression, BindParameter
from sqlalchemy.sql.util import find_tables

from app.domain.conversation.numbering import (
    CREATED_KEY,
    NUMBERING_HELD_KEY,
    hold_numbering_now,
    lock_conversations,
    needed_in_flush,
)
from app.domain.room_task.models import Task

_TASKS = "tasks"


async def lock_task_for_change(
    session: AsyncSession, task_id: uuid.UUID, *, also: Iterable[uuid.UUID] = ()
) -> Task | None:
    """``task_id`` read for a change: its conversation's numbering (a task's
    conversation id is its own) with ``also`` in one batch, then its row FOR
    NO KEY UPDATE, read again from the database. None when there is no such
    task."""
    found = await lock_tasks_for_change(session, [task_id], also=also)
    return found[0] if found else None


async def lock_tasks_for_change(
    session: AsyncSession,
    task_ids: Iterable[uuid.UUID],
    *,
    also: Iterable[uuid.UUID] = (),
) -> list[Task]:
    """Every one of ``task_ids`` read for a change: their numbering with
    ``also`` (every other conversation the change will write into) in one
    batch, then each row FOR NO KEY UPDATE in id order. The tasks that exist,
    in that order."""
    ordered = sorted(set(task_ids), key=str)
    around = await session.run_sync(_said_around, ordered)
    await lock_conversations(session, [*ordered, *around, *also])
    found = []
    for task_id in ordered:
        task = await session.get(
            Task, task_id, with_for_update={"key_share": True}, populate_existing=True
        )
        if task is not None:
            found.append(task)
    return found


_THREADS = table("threads", column("id", Uuid), column("root_block_id", Uuid))
_TASK_ROWS = table(
    "tasks",
    column("id", Uuid),
    column("room_id", Uuid),
    column("upgraded_from_block_id", Uuid),
)


def _said_around(session: Session, task_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """Where a change to these tasks also says something: each one's room
    (how it ended, what it left undone) and the 支线 under the message it was
    made from (`review.task_landing.tell_origin`). A 支线 not opened yet is
    opened by the change itself, and needs no lock. Read on the session's own
    connection: it runs inside a flush or a statement's execution too."""
    task_ids = list(task_ids)
    if not task_ids:
        return set()
    rows: list[tuple[uuid.UUID, uuid.UUID | None]] = []
    missing: list[uuid.UUID] = []
    for tid in task_ids:
        cached = session.identity_map.get(session.identity_key(Task, (tid,)))
        if cached is not None:
            rows.append((cached.room_id, cached.upgraded_from_block_id))
        else:
            missing.append(tid)
    connection = session.connection()
    if missing:
        rows.extend(
            (r[0], r[1])
            for r in connection.execute(
                select(_TASK_ROWS.c.room_id, _TASK_ROWS.c.upgraded_from_block_id).where(
                    _TASK_ROWS.c.id.in_(missing)
                )
            ).all()
        )
    around = {room_id for room_id, _origin in rows}
    origins = [origin for _room, origin in rows if origin is not None]
    if origins:
        by_root = {
            getattr(obj, "root_block_id", None): getattr(obj, "id", None)
            for obj in session.identity_map.values()
            if getattr(type(obj), "__tablename__", None) == "threads"
        }
        missing_origins: list[uuid.UUID] = []
        for origin in origins:
            tid = by_root.get(origin)
            if tid is not None:
                around.add(tid)
            else:
                missing_origins.append(origin)
        if missing_origins:
            around.update(
                connection.execute(
                    select(_THREADS.c.id).where(
                        _THREADS.c.root_block_id.in_(missing_origins)
                    )
                ).scalars()
            )
    return around


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
    # A task named here and not yet held: its room and origin 支线 come in the
    # same batch, as with `lock_task_for_change`.
    hold_numbering_now(
        state.session, [*ids, *_said_around(state.session, _fresh(state.session, ids))]
    )


@needed_in_flush
def _task_rows_in_flush(session: Session) -> list[uuid.UUID]:
    written = [
        *(
            row.id
            for row in session.dirty
            if isinstance(row, Task) and session.is_modified(row)
        ),
        *(row.id for row in session.deleted if isinstance(row, Task)),
    ]
    return [*written, *_said_around(session, _fresh(session, written))]


def _fresh(session: Session, task_ids) -> list[uuid.UUID]:
    """The tasks among these whose numbering this transaction does not hold
    yet and that it did not create: a change reaching them now brings their
    room and origin 支线 into the same batch."""
    held = session.info.get(NUMBERING_HELD_KEY, {})
    created = session.info.get(CREATED_KEY, ())
    return [one for one in task_ids if one not in held and one not in created]
