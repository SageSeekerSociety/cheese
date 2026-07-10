"""Space data access."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import Space, SpaceKind


class SpaceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, *, name: str, kind: SpaceKind, description: str) -> Space:
        space = Space(name=name, kind=kind, description=description)
        self._session.add(space)
        await self._session.flush()
        await self._session.refresh(space)
        return space

    async def get(self, space_id: uuid.UUID) -> Space | None:
        return await self._session.get(Space, space_id)

    async def list_all(self) -> list[Space]:
        stmt = select(Space).order_by(Space.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def count(self) -> int:
        return int(
            (await self._session.scalar(select(func.count()).select_from(Space))) or 0
        )
