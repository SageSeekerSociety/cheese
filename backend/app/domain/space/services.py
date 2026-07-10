"""Space business logic."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.space.models import Space, SpaceKind
from app.domain.space.repositories import SpaceRepository


class SpaceService:
    def __init__(self, session: AsyncSession):
        self._repo = SpaceRepository(session)

    async def create(self, *, name: str, kind: SpaceKind, description: str) -> Space:
        return await self._repo.add(name=name, kind=kind, description=description)

    async def get_or_404(self, space_id: uuid.UUID) -> Space:
        space = await self._repo.get(space_id)
        if space is None:
            raise NotFoundError("Space not found")
        return space

    async def list_all(self) -> tuple[list[Space], int]:
        return await self._repo.list_all(), await self._repo.count()
