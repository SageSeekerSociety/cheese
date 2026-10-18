"""Facts one request reads once and may ask for again.

A request handles a single `AsyncSession` from beginning to end: FastAPI hands
every dependency the same one. Several of them ask the same question — where
someone stands in a project, and so what they may do there — and each answer
costs five to a dozen statements, depending on where the person sits. The
session remembers those answers in `session.info`
under `REQUEST_FACTS` (the same place `core/live_frames` parks what it owes a
page), so the second and third ask read the first answer.

The answers are forgotten whenever the session writes or takes a write back:

* a flush may change who is where — someone added to or removed from the
  roster, excluded, given a role — so an answer read before it is stale;
* a write sent as a statement does too, and it is not the same event: a Core
  ``INSERT``/``UPDATE``/``DELETE`` leaves nothing pending in the unit of work,
  so ``flush()`` returns without ever emitting ``before_flush``.
  ``MemberRepository.exclude`` writes the exclusion that way. Every DML
  statement executed through the session forgets the answers before it runs;
* a commit ends the transaction those answers were read in, so the next read
  is a new one that may see what other requests committed in between — and so
  does closing the session, which `core.db.release_read_session` does before
  waiting on remote I/O;
* a rollback of the session's transaction drops everything the request read;
* a rollback of a savepoint drops it too. Releasing a savepoint commits
  nothing, but a savepoint that rolls back may have carried a change someone
  asked about; re-reading is cheap next to answering from a state that no
  longer holds.

The cache belongs to the session, and a session belongs to one request. It
does not see what another request commits: an answer read before that commit
is served unchanged until this session writes or ends its transaction. Under
READ COMMITTED a fresh SELECT would see the new row, so the cache deliberately
trades that intra-request freshness for fewer statements; the staleness is
bounded by one request. A second ``standing()`` call is not a way to observe
another request's change — end the transaction if freshness matters. Every
write this session makes, and every transaction boundary, drops what it read.
"""

from typing import Any

from sqlalchemy import Delete, Insert, Update, event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

#: The `session.info` slot: every fact this request has already read.
REQUEST_FACTS = "request_facts_read_once"


def recall(session: AsyncSession, key: Any) -> tuple[bool, Any]:
    """``(True, value)`` for a fact this session already read, else ``(False, None)``.

    A remembered ``None`` is an answer too: "this person is not in this
    project" is asked as often as the positive one.
    """
    facts: dict[Any, Any] | None = session.info.get(REQUEST_FACTS)
    if not facts or key not in facts:
        return False, None
    return True, facts[key]


def remember(session: AsyncSession, key: Any, value: Any) -> None:
    """Keep ``value`` as this session's answer to ``key`` until it writes."""
    session.info.setdefault(REQUEST_FACTS, {})[key] = value


def forget(session: Session) -> None:
    """Drop every fact read so far."""
    session.info.pop(REQUEST_FACTS, None)


@event.listens_for(Session, "before_flush")
def _forget_before_write(session: Session, _flush_context, _instances) -> None:
    # Before, not after: the flush's own reads must not see an answer that the
    # rows it is about to write have already contradicted.
    forget(session)


@event.listens_for(Session, "do_orm_execute")
def _forget_before_dml(state: ORMExecuteState) -> None:
    # ``flush()`` emits nothing when the unit of work is empty, and a Core DML
    # leaves it empty — so a write sent as a statement would otherwise keep an
    # answer it just contradicted.
    if isinstance(state.statement, (Insert, Update, Delete)):
        forget(state.session)


@event.listens_for(Session, "after_commit")
def _forget_after_commit(session: Session) -> None:
    forget(session)


@event.listens_for(Session, "after_rollback")
def _forget_after_rollback(session: Session) -> None:
    forget(session)


@event.listens_for(Session, "after_transaction_end")
def _forget_after_transaction_ends(session: Session, transaction) -> None:
    # A closed session ends its transaction without emitting a commit or a
    # rollback: `core.db.release_read_session` closes one before waiting on
    # remote I/O and then keeps reading on it. Only the session's own level
    # counts — a released savepoint commits nothing and changes nothing.
    if transaction.parent is None:
        forget(session)


@event.listens_for(Session, "after_soft_rollback")
def _forget_after_soft_rollback(session: Session, _previous_transaction) -> None:
    forget(session)
