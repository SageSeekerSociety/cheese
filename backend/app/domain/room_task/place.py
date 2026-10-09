"""Resolve conversation addresses: a room, or a task or a 支线 in a room."""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.living_doc.services import Documents
from app.domain.room_task.models import Task
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic


@dataclass(frozen=True)
class Place:
    """Where a conversation happens: a room, or one of its tasks or 支线.

    ``room`` is always the room — a task works in the room it hangs in, a 支线
    hangs under one of its messages, and the room is what answers for their
    roster and their machine. ``task`` is set when the conversation is a task's
    own, ``thread`` when it is a 支线's.
    """

    room: Topic
    task: Task | None = None
    thread: Thread | None = None

    @property
    def room_id(self) -> uuid.UUID:
        return self.room.id

    @property
    def task_id(self) -> uuid.UUID | None:
        return self.task.id if self.task is not None else None

    @property
    def thread_id(self) -> uuid.UUID | None:
        return self.thread.id if self.thread is not None else None

    @property
    def inner_id(self) -> uuid.UUID | None:
        """The conversation's id when it is one inside the room — a task's or a
        支线's — rather than the room's own line."""
        return self.task_id or self.thread_id

    @property
    def conversation_id(self) -> uuid.UUID:
        """The conversation's own id: the task's, the 支线's, or the room's."""
        return self.inner_id or self.room.id

    @property
    def project_id(self) -> uuid.UUID:
        return self.room.project_id

    @property
    def title(self) -> str:
        return self.task.title if self.task is not None else self.room.title

    @property
    def keeps_work(self) -> bool:
        """Whether what a session does here may reach the project. Not from a
        支线, and from a task only once its owner started it: what changes the
        project is done in a started task or in the room's own line."""
        if self.thread is not None:
            return False
        return self.task is None or self.task.started_at is not None


class PlaceResolver:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def resolve(self, place_id: uuid.UUID) -> Place | None:
        """The room ``place_id`` names. A task's id names no room: routes
        addressed by room stay closed to it."""
        room = await self._session.get(Topic, place_id)
        return Place(room=room) if room is not None else None

    async def conversation(self, conversation_id: uuid.UUID) -> Place | None:
        """The conversation ``conversation_id`` names — a room's id, a task's or
        a 支线's."""
        place = await self.resolve(conversation_id)
        if place is not None:
            return place
        task = await self._session.get(Task, conversation_id)
        if task is not None:
            room = await self._session.get(Topic, task.room_id)
            return Place(room=room, task=task) if room is not None else None
        thread = await self._session.get(Thread, conversation_id)
        if thread is not None:
            room = await self._session.get(Topic, thread.room_id)
            return Place(room=room, thread=thread) if room is not None else None
        return None


async def living_doc_of(session: AsyncSession, place: Place):
    """The conversation's living document. Only a task has one, and not until
    it is asked for; a channel, a private chat and a 支线 have none."""

    if place.task is None or place.task.document_id is None:
        return None
    return await Documents(session).get(place.task.document_id)


async def doc_text_of(
    session: AsyncSession, place: Place, *, needs_place: bool
) -> str | None:
    """The conversation's living document as the system prompt is told about it.

    A conversation that works on a machine and whose document is still empty
    answers `""`, not None: the prompt then tells the teammate to write the
    first version (`build_system_prompt`). Only a task has such a document.
    """
    if place.task is None:
        return None
    doc = await living_doc_of(session, place)
    text = doc.content if doc else None
    if needs_place and not (text or "").strip():
        text = ""
    return text


async def session_keeps_work(
    session: AsyncSession, session_id: str | uuid.UUID | None
) -> bool:
    """Whether the agent session ``session_id`` may carry its work into the
    project (`Place.keeps_work`): what a machine credential naming it asks
    before it is handed what pushes. One naming no session, or a session that
    is gone, has no 支线 or unstarted task to answer for, and keeps its work.

    ``session_id`` is whatever the caller holds — a claim read off a token, or
    the id of the session row it looked up, which is a UUID."""

    try:
        row = await session.get(AgentSession, uuid.UUID(str(session_id)))
    except ValueError:
        return True
    if row is None:
        return True
    place = await PlaceResolver(session).conversation(row.conversation_id)
    return place is None or place.keeps_work
