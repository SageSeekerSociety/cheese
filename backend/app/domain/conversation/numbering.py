"""One order for every conversation's numbering lock.

Every block written into a conversation takes that conversation's numbering
lock (the advisory lock of `block_numbered()`, alembic `0dd66b328211`) and holds
it until its transaction ends: the next block's number is read from the
largest one, and a reader's cursor must never pass a number still to commit.
Two transactions that each write into two conversations, in opposite orders,
each hold one lock the other waits for, and PostgreSQL aborts one of them.

So a transaction takes its numbering locks in one batch:

- The first time it takes any — `lock_conversations` named explicitly, a
  block it inserts, a task row it writes (`room_task.lock_order`) — it takes
  every one it will need, in conversation id order, in one call.
- From then on it writes only into conversations it holds. Asking for one more
  is refused (`NumberingOutOfOrder`), whatever the ids: a writer that reaches
  a second conversation later fails every time it runs, not only when the ids
  happen to fall in the wrong order.
- A conversation created in this transaction (its room, task, 支线 or 芝士
  conversation row inserted here, through the ORM or a Core INSERT that names
  its id) is left out: nobody else can write into it before this transaction
  commits.

Two transactions over overlapping conversations then meet at the first one
they share, and the one that waits holds nothing the other needs.

The refusal raises where `settings.strict_numbering_order` is set (the
tests). Elsewhere it is logged as an error and the lock is taken as it would
be without this rule: a writer this rule missed keeps working as it did, and
the log names it.

What a transaction holds is recorded per savepoint: PostgreSQL lets a lock go
when the savepoint it was taken in is rolled back, and keeps it when the
savepoint is released.
"""

import logging
import traceback
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import greenlet
from sqlalchemy import Insert, Uuid, column, event, select, table, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

from app.core.config import settings

logger = logging.getLogger(__name__)

#: The first key of every conversation's numbering lock (`block_numbered()`).
NUMBERING_LOCK = 1_734_632_801

#: `Session.info` key: the conversations whose numbering this transaction
#: holds, each with the savepoint it was taken in (None: outside any).
NUMBERING_HELD_KEY = "numbering_held"

#: `Session.info` key: conversations created in this transaction.
CREATED_KEY = _CREATED = "conversations_created"

#: The tables whose inserted row registers a conversation of the same id
#: (`conversation.models`, migration ``f7985445d2bf``).
_CONVERSATION_TABLES = frozenset(
    {"topics", "tasks", "threads", "assistant_conversations"}
)

_TAKE = text(
    "SELECT pg_advisory_xact_lock(:key, hashtext(CAST(:conversation AS text)))"
)

#: Other writes a flush makes that need a conversation's numbering first (a
#: task row: `room_task.lock_order`). Each returns the conversations it needs.
_FLUSH_NEEDS: list[Callable[[Session], Iterable[uuid.UUID]]] = []


class NumberingOutOfOrder(RuntimeError):
    """A transaction holding numbering locks asked for another one."""


def needed_in_flush(collect: Callable[[Session], Iterable[uuid.UUID]]):
    """Register what else a flush writes that needs its numbering first; taken
    in the same batch as the conversations of the blocks it inserts."""
    _FLUSH_NEEDS.append(collect)
    return collect


def hold_numbering_now(session: Session, conversations: Iterable[uuid.UUID]) -> None:
    """Take these conversations' numbering locks on ``session``'s connection,
    in id order. Synchronous, for code already inside a flush or a
    statement's execution; `lock_conversations` is the async form."""
    held: dict[uuid.UUID, object] = session.info.setdefault(NUMBERING_HELD_KEY, {})
    created = session.info.get(_CREATED, ())
    wanted = set(conversations) - held.keys() - set(created)
    if not wanted:
        return
    if held:
        said = (
            f"this transaction holds the numbering of {sorted(map(str, held))} "
            f"and asked for {sorted(map(str, wanted))}: name every conversation "
            "it will write into in one call, first (conversation.numbering)"
        )
        if settings.strict_numbering_order:
            raise NumberingOutOfOrder(said)
        logger.error(
            "numbering lock out of order: %s\nasked from:\n%s", said, _asked_from()
        )
    savepoint = session.get_nested_transaction()
    # Only PostgreSQL numbers blocks (`block_numbered()`); elsewhere (the unit
    # tests' SQLite) there is no lock to take, and the order is still kept.
    numbered = session.get_bind().dialect.name == "postgresql"
    for conversation in sorted(wanted, key=str):
        if numbered:
            session.connection().execute(
                _TAKE, {"key": NUMBERING_LOCK, "conversation": str(conversation)}
            )
        held[conversation] = savepoint


def _asked_from() -> str:
    """The stack of the code that asked, the async caller included.

    A flush or a `run_sync` runs this listener in a greenlet SQLAlchemy
    spawned for the sync side; its own stack ends at that handoff. The caller
    — the coroutine that awaited the flush, and every coroutine awaiting it —
    is suspended in the parent greenlet, at the switch, with its frames still
    linked: that stack is the one that names the writer."""
    here = traceback.format_stack()[:-1]
    parent = getattr(greenlet.getcurrent(), "parent", None)
    frame = getattr(parent, "gr_frame", None)
    if frame is None:
        return "".join(here)
    return (
        "".join(traceback.format_stack(frame)) + "  -- sync side --\n" + "".join(here)
    )


async def lock_conversations(
    session: AsyncSession, conversations: Iterable[uuid.UUID]
) -> None:
    """Take every conversation this transaction will write into, at once and
    before anything else is locked; held until it ends."""
    await session.run_sync(hold_numbering_now, list(conversations))


async def hold_numbering(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    """`lock_conversations` for one conversation."""
    await lock_conversations(session, [conversation_id])


@event.listens_for(Session, "before_flush")
def _numbering_before_inserts(session: Session, _context, _instances) -> None:
    needed = {
        row.conversation_id
        for row in session.new
        if getattr(type(row), "__tablename__", None) == "blocks"
        and row.conversation_id is not None
    }
    for collect in _FLUSH_NEEDS:
        needed.update(collect(session))
    if needed:
        hold_numbering_now(session, needed)


def _inserted_ids(statement: Insert, parameters: Any) -> list[uuid.UUID]:
    """The ids a Core INSERT names for its rows: its VALUES, or the parameter
    sets it is executed with. An id named here is either inserted by this
    transaction or, when the insert lost to a conflict, by nobody: either way
    nobody else can be writing into it."""
    found: list[Any] = []
    multi = getattr(statement, "_multi_values", ()) or ()
    for rows in multi:
        for row in rows:
            found.extend(v for k, v in row.items() if getattr(k, "key", k) == "id")
    for key, value in (getattr(statement, "_values", None) or {}).items():
        if getattr(key, "key", key) == "id":
            found.append(getattr(value, "value", value))
    sets = parameters if isinstance(parameters, list) else [parameters]
    for given in sets:
        if isinstance(given, Mapping) and "id" in given:
            found.append(given["id"])
    return [uuid.UUID(str(value)) for value in found if value is not None]


@event.listens_for(Session, "do_orm_execute")
def _note_inserted_conversations(state: ORMExecuteState) -> None:
    statement = state.statement
    if isinstance(statement, Insert) and (
        getattr(statement.table, "name", None) in _CONVERSATION_TABLES
    ):
        created = _new_conversations(
            state.session,
            statement.table.name,
            _inserted_ids(statement, state.parameters),
        )
        if created:
            state.session.info.setdefault(_CREATED, set()).update(created)


_THREADS = table("threads", column("id", Uuid))


def _new_conversations(session: Session, table_name: str, ids: list[uuid.UUID]):
    """The ones of ``ids`` that a row inserted into ``table_name`` makes a new
    conversation. A task inserted under a 支线's id is that 支线 turned into a
    task in place (`room_task.dispatch.create_task`): its conversation was
    there before, and others may be writing into it."""
    if table_name != "tasks" or not ids:
        return ids
    existing = set(
        session.connection()
        .execute(select(_THREADS.c.id).where(_THREADS.c.id.in_(ids)))
        .scalars()
    )
    return [one for one in ids if one not in existing]


@event.listens_for(Session, "after_flush")
def _note_created_conversations(session: Session, _context) -> None:
    by_table: dict[str, list[uuid.UUID]] = {}
    for row in session.new:
        name = getattr(type(row), "__tablename__", None)
        if name in _CONVERSATION_TABLES:
            by_table.setdefault(name, []).append(row.id)
    for name, ids in by_table.items():
        created = _new_conversations(session, name, ids)
        if created:
            session.info.setdefault(_CREATED, set()).update(created)


@event.listens_for(Session, "after_rollback")
def _numbering_rolled_back(session: Session) -> None:
    # Fired while the transaction rolling back is still the innermost one.
    held = session.info.get(NUMBERING_HELD_KEY)
    if not held:
        return
    savepoint = session.get_nested_transaction()
    if savepoint is None:
        held.clear()
        return
    for conversation in [c for c, taken in held.items() if taken is savepoint]:
        del held[conversation]


@event.listens_for(Session, "after_transaction_end")
def _numbering_released(session: Session, transaction) -> None:
    if transaction.parent is None:
        session.info.pop(NUMBERING_HELD_KEY, None)
        session.info.pop(_CREATED, None)
        return
    held = session.info.get(NUMBERING_HELD_KEY)
    if not held or not transaction.nested:
        return
    # A savepoint released: what was taken in it is held by the one around it.
    outer = transaction.parent
    while outer is not None and not outer.nested:
        outer = outer.parent
    for conversation, taken in held.items():
        if taken is transaction:
            held[conversation] = outer
