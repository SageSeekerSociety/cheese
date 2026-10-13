"""Caller-awaited session turn events, received by the work runner.

The API composition root supplies the receiver to ChatService. Opening an
interval is awaited in place; output and completion update the runner's memory
synchronously, after the caller's persistence/publication has succeeded. This is
one process-local collaborator, not a detached pubsub or an asynchronous bus.
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SessionTurnSource(Protocol):
    """The session store on which a self-started interval is written."""

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]: ...


class SessionTurnEvents(Protocol):
    """The three notifications from a session subscription to its runner."""

    async def open_turn_the_session_started(
        self,
        chat_service: SessionTurnSource,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        agent_handle: str,
        session_id: str | None = None,
    ) -> None: ...

    def note_session_output(self, turn_id: uuid.UUID, *, tool: bool) -> None: ...

    def close_turn_the_session_started(self, turn_id: uuid.UUID) -> None: ...
