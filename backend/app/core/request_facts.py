"""Facts one request reads once, across every session it opens.

FastAPI dependencies share a session, but message persistence opens another.
The cache lives in a ContextVar, not ``session.info``. HTTP requests use
query_watch's scope identity and closed flag; non-HTTP work uses its asyncio
task (or query_watch.request_scope for an explicit boundary). A completed
request's inherited context cannot keep serving its answers.

Flush, commit, rollback, close and savepoint rollback invalidate the facts.
Core INSERT/UPDATE/DELETE only invalidate dependencies of their target table;
unknown targets or opaque SQL invalidate everything. Callers declare semantic
dependencies, including identity-map reads that emit no SELECT. Undeclared
dependencies conservatively mean every table. An unrelated idempotency INSERT
can thus retain a roster without exempting a business write.

Session-bound ORM results and answers affected by uncommitted writes stay
usable only on their originating session, in this same cache. Value containers
are copied so one caller cannot mutate another's answer. Other requests'
commits are observed when this request ends its own transaction; under READ
COMMITTED, end the transaction when freshness matters.
"""

from asyncio import current_task
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Delete, Insert, Update, event, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstanceState, ORMExecuteState, Session
from sqlalchemy.sql.elements import TextClause

from app.core.query_watch import current_watch


@dataclass(frozen=True)
class _Fact:
    value: Any
    tables: frozenset[str] | None
    owner: Session | None
    orm: bool


@dataclass
class _Facts:
    scope: object
    values: dict[Any, _Fact] = field(default_factory=dict)
    writes: dict[Session, set[str]] = field(default_factory=dict)


_current: ContextVar[_Facts | None] = ContextVar("request_facts", default=None)


def _facts() -> _Facts | None:
    scope: object | None
    watch = current_watch()
    if watch is not None:
        if watch.closed:
            _current.set(None)
            return None
        scope = watch
    else:
        # Session events also run in synchronous workers with no asyncio loop.
        try:
            scope = current_task()
        except RuntimeError:
            return None
        if scope is None:
            return None
    facts = _current.get()
    if facts is None or facts.scope is not scope:
        facts = _Facts(scope)
        _current.set(facts)
    return facts


def _has_orm(value: Any) -> bool:
    if isinstance(inspect(value, raiseerr=False), InstanceState):
        return True
    if isinstance(value, dict):
        return any(_has_orm(item) for pair in value.items() for item in pair)
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_has_orm(item) for item in value)
    return False


def _affected(writes: set[str], tables: frozenset[str] | None) -> bool:
    return bool(writes) and (
        tables is None or "*" in writes or not writes.isdisjoint(tables)
    )


def recall(session: AsyncSession, key: Any) -> tuple[bool, Any]:
    """``(True, value)`` for an answer, including None, else ``(False, None)``."""
    # A hit must not bypass a SELECT's autoflush after pending ORM changes.
    if session.autoflush and (session.new or session.dirty or session.deleted):
        return False, None
    facts = _facts()
    fact = facts.values.get(key) if facts is not None else None
    if fact is None or facts is None:
        return False, None
    owner = session.sync_session
    if fact.owner is not None and fact.owner is not owner:
        return False, None
    if fact.owner is None and _affected(facts.writes.get(owner, set()), fact.tables):
        return False, None
    return True, fact.value if fact.orm else deepcopy(fact.value)


def remember(
    session: AsyncSession,
    key: Any,
    value: Any,
    *,
    tables: frozenset[str] | None = None,
) -> None:
    """Remember a fact, sharing values only where transaction visibility permits."""
    facts = _facts()
    if facts is None:
        return
    orm = _has_orm(value)
    owner = session.sync_session
    private = orm or _affected(facts.writes.get(owner, set()), tables)
    facts.values[key] = _Fact(
        value if orm else deepcopy(value), tables, owner if private else None, orm
    )


def forget(session: Session, *, written_table: str | None = None) -> None:
    """Invalidate the current request, including facts read by another session."""
    facts = _facts()
    if facts is None:
        return
    if written_table is None:
        facts.values.clear()
    else:
        for key, fact in list(facts.values.items()):
            if fact.tables is None or written_table in fact.tables:
                del facts.values[key]


def _write(session: Session, table: str | None = None) -> None:
    facts = _facts()
    if facts is not None:
        facts.writes.setdefault(session, set()).add(table or "*")
    forget(session, written_table=table)


@event.listens_for(Session, "before_flush")
def _forget_before_write(session: Session, _flush_context, _instances) -> None:
    # The flush's own reads must not see an answer its writes contradicted.
    _write(session)


@event.listens_for(Session, "do_orm_execute")
def _forget_before_dml(state: ORMExecuteState) -> None:
    # Core writes leave the unit of work empty: before_flush will not run.
    if isinstance(state.statement, (Insert, Update, Delete)):
        _write(state.session, getattr(state.statement.table, "name", None))
    elif isinstance(state.statement, TextClause):
        _write(state.session)


@event.listens_for(Session, "after_commit")
def _forget_after_commit(session: Session) -> None:
    if not session.in_nested_transaction():
        forget(session)


@event.listens_for(Session, "after_rollback")
def _forget_after_rollback(session: Session) -> None:
    forget(session)


@event.listens_for(Session, "after_transaction_end")
def _forget_after_transaction_ends(session: Session, transaction) -> None:
    # Closing a session ends its transaction without a commit/rollback event.
    # Savepoint release must not forget the outer transaction's writes.
    if transaction.parent is None:
        facts = _facts()
        if facts is not None:
            facts.writes.pop(session, None)
        forget(session)


@event.listens_for(Session, "after_soft_rollback")
def _forget_after_soft_rollback(session: Session, _previous_transaction) -> None:
    forget(session)
