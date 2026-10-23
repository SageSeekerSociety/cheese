"""What a 芝士 changed while answering a question, and undoing it.

Every write a 芝士 makes into something of the person's — a 题目 draft, a
living document — is kept as one ``AssistantChange`` under the answer it was
made in (``report``). The panel and the page show them, with 撤销 next to each,
and undoing one is the same call whatever was written
(``POST /assistant/changes/{id}/undo``).

What a write changed, and how it is put back, is the business of what was
written: each kind registers itself (``register``) from the module that owns
the writes — the draft routes, the document routes — with

- ``parts``: the keys the person can undo one by one (a draft's fields; a
  document's write is one part), read off a change's payload;
- ``apply``: puts the parts back as they were before the answer (or, to redo,
  as the answer left them), as the person, through the same checks a write of
  theirs goes through. It gets every change of the answer to the same target,
  in order, since 芝士 may have written one field several times: undoing it
  goes back to before the first.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.assistant.models import AssistantChange

Apply = Callable[
    [AsyncSession, Any, Sequence[AssistantChange], list[str], bool],
    Awaitable[dict],
]


@dataclass(frozen=True)
class Kind:
    parts: Callable[[dict], list[str]]
    apply: Apply


_KINDS: dict[str, Kind] = {}


def register(kind: str, *, parts: Callable[[dict], list[str]], apply: Apply) -> None:
    _KINDS[kind] = Kind(parts, apply)


async def report(
    db: AsyncSession,
    *,
    user_id: int,
    work: str,
    kind: str,
    target: object,
    payload: dict,
) -> AssistantChange:
    """Keep one write of the answer ``work``, made for ``user_id``. Flushed,
    so it has its id; the caller commits."""
    row = AssistantChange(
        user_id=user_id,
        work=work,
        kind=kind,
        target=str(target),
        payload=payload,
        undone=[],
    )
    db.add(row)
    await db.flush()
    return row


def view(row: AssistantChange) -> dict:
    """A change as the panel and the page take it: what its kind says it
    changed, with its id and the parts already undone."""
    return {**row.payload, "id": row.id, "kind": row.kind, "undone": list(row.undone)}


async def of_work(db: AsyncSession, work: str) -> list[AssistantChange]:
    """The writes of one answer, in the order they were made."""
    rows = await db.execute(
        select(AssistantChange)
        .where(AssistantChange.work == work)
        .order_by(AssistantChange.id)
    )
    return list(rows.scalars())


async def undone_of(db: AsyncSession, user_id: int, ids: list[int]) -> dict[int, list]:
    """What is undone now of these changes of the person's: a kept answer
    carries its changes as they were told, before anything was undone."""
    if not ids:
        return {}
    rows = await db.execute(
        select(AssistantChange.id, AssistantChange.undone).where(
            AssistantChange.id.in_(ids), AssistantChange.user_id == user_id
        )
    )
    return {change_id: list(undone) for change_id, undone in rows.all()}


async def undo(
    db: AsyncSession,
    asker: Any,
    *,
    user_id: int,
    change_id: int,
    part: str | None,
    redo: bool = False,
) -> dict:
    """Undo one part of a change (every part still standing, when ``part`` is
    None), or redo what was undone. ``asker`` is passed to the kind's
    ``apply`` to act as the person. Returns what ``apply`` says, and every
    change it touched as ``view`` has it now."""
    row = await db.get(AssistantChange, change_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError.for_resource("change", change_id)
    kind = _KINDS.get(row.kind)
    if kind is None:
        raise BadRequestError(say("assistantChangeCannotUndo"))
    parts = kind.parts(row.payload)
    if part is not None and part not in parts:
        raise BadRequestError(say("assistantChangeNoSuchPart"))
    undone = set(row.undone)
    wanted = [part] if part is not None else parts
    chosen = [p for p in wanted if (p in undone) == redo]
    if not chosen:
        raise BadRequestError(
            say("assistantChangeNotUndone" if redo else "assistantChangeAlreadyUndone")
        )
    same = [
        r
        for r in await of_work(db, row.work)
        if r.kind == row.kind and r.target == row.target and r.user_id == user_id
    ]
    said = await kind.apply(db, asker, same, chosen, redo)
    touched: list[AssistantChange] = []
    for r in same:
        mine = set(kind.parts(r.payload)) & set(chosen)
        if not mine:
            continue
        now = set(r.undone) - mine if redo else set(r.undone) | mine
        r.undone = [p for p in kind.parts(r.payload) if p in now]
        touched.append(r)
    await db.commit()
    return {**said, "changes": [view(r) for r in touched]}
