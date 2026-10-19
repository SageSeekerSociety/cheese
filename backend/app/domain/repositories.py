"""Shared read surface for domain repositories.

A repository subclasses ``Repository[Model]``, names its ``model``, and
overrides ``base_statement`` when every read of it carries the same filter
(soft delete, for one). ``get_by_id`` and ``get_by_ids`` then come from here,
both on that one statement, so the single read and its batch sibling cannot
drift apart and a new repository has its batch read before anyone asks.

Writes stay in each repository: this class only settles how rows are read.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.reads import HasId, rows_by_ids


class Repository[RowT: HasId[Any]]:
    model: type[RowT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def base_statement(self) -> Select[tuple[RowT]]:
        """Every read starts here; override to add the filters all reads share."""
        return select(self.model)

    async def get_by_id(self, row_id: Hashable) -> RowT | None:
        stmt = self.base_statement().where(self.model.id == row_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids[IdT: Hashable](self, ids: Iterable[IdT]) -> dict[IdT, RowT]:
        return await rows_by_ids(self._session, self.base_statement(), self.model, ids)
