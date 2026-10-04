"""Resolve conversation addresses: a room, or a task in a room."""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.room_task.models import Task
from app.domain.topic.models import Topic


@dataclass(frozen=True)
class Place:
    """Where a conversation happens: a room, or one of its tasks.

    ``room`` is always the room — a task works in the room it hangs in, and the
    room is what answers for its roster, its machine and its messages' table.
    ``task`` is set when the conversation is the task's own.
    """

    room: Topic
    task: Task | None = None

    @property
    def room_id(self) -> uuid.UUID:
        return self.room.id

    @property
    def task_id(self) -> uuid.UUID | None:
        return self.task.id if self.task is not None else None

    @property
    def conversation_id(self) -> uuid.UUID:
        """The conversation's own id: the task's, or the room's."""
        return self.task.id if self.task is not None else self.room.id

    @property
    def project_id(self) -> uuid.UUID:
        return self.room.project_id

    @property
    def title(self) -> str:
        return self.task.title if self.task is not None else self.room.title


class PlaceResolver:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def resolve(self, place_id: uuid.UUID) -> Place | None:
        """The room ``place_id`` names. A task's id names no room: routes
        addressed by room stay closed to it."""
        room = await self._session.get(Topic, place_id)
        return Place(room=room) if room is not None else None

    async def conversation(self, conversation_id: uuid.UUID) -> Place | None:
        """The conversation ``conversation_id`` names — a room's id or a task's."""
        place = await self.resolve(conversation_id)
        if place is not None:
            return place
        task = await self._session.get(Task, conversation_id)
        if task is None:
            return None
        room = await self._session.get(Topic, task.room_id)
        return Place(room=room, task=task) if room is not None else None
