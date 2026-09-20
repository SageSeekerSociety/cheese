"""Resolve conversation addresses. Task workspaces are addressed separately."""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topic.models import Topic


@dataclass(frozen=True)
class Place:
    room: Topic

    @property
    def room_id(self) -> uuid.UUID:
        return self.room.id

    @property
    def project_id(self) -> uuid.UUID:
        return self.room.project_id

    @property
    def title(self) -> str:
        return self.room.title


class PlaceResolver:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def resolve(self, place_id: uuid.UUID) -> Place | None:
        room = await self._session.get(Topic, place_id)
        return Place(room=room) if room is not None else None
