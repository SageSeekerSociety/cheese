"""Shared batch-read primitive for domain repositories.

Across ``app/domain``, single-row ``get_by_id`` used to be the only ready-to-hand
lookup while batch reads were hand-rolled per repository under dozens of names.
This module makes a single ``IN`` query the shortest path and standardizes the
repository method name and signature on ``get_by_ids(ids) -> dict[id, row]``.

A repository that offers a single-row ``get_by_id`` should offer the batch
sibling ``get_by_ids`` built on this primitive, with the same filters.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from typing import Any, Protocol

from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped


class _HasId[IdT: Hashable](Protocol):
    id: Mapped[IdT]


async def get_by_ids[IdT: Hashable, RowT: _HasId[Any]](
    session: AsyncSession,
    model: type[RowT],
    ids: Iterable[IdT],
    *where: ColumnElement[bool],
) -> dict[IdT, RowT]:
    """Read rows of ``model`` matching ``ids`` in a single ``IN`` query.

    Deduplicates ``ids`` in first-seen order, skips the query when ``ids`` is
    empty, applies any caller-supplied predicates (such as
    ``Model.deleted_at.is_(None)``), and returns ``{row.id: row}`` with missing
    or filtered-out ids omitted.
    """
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        return {}
    stmt: Select[tuple[RowT]] = select(model).where(
        model.id.in_(unique_ids),
        *where,
    )
    result = await session.execute(stmt)
    return {row.id: row for row in result.scalars().all()}
