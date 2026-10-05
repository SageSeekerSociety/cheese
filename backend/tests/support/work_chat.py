"""Admission methods shared by controllable ChatService turn doubles.

The runner still uses the real seat resolution, admission lock and durable-input
queries. Only the streaming turn is supplied by each test's double.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.domain.agent_instance.services import ResolvedAgent
    from app.domain.room_task.place import Place
    from app.domain.topic.models import Topic


class WorkChat:
    """Supply admission without constructing a compute-backed ChatService."""

    session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def _sessions(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is None:
            raise RuntimeError("A turn double needs its database session factory")
        return self.session_factory

    async def work_policy(self, topic_id: uuid.UUID) -> dict | None:
        return None

    async def _turn_seat_handle(
        self,
        topic_id: uuid.UUID,
        *,
        user_block_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
    ) -> str:
        from app.domain.agent.chat import ChatService

        return await ChatService._turn_seat_handle(
            cast(ChatService, self),
            topic_id,
            user_block_id=user_block_id,
            recipient_instance_id=recipient_instance_id,
            recipient_handle=recipient_handle,
        )

    def _seat_lock_for(self, topic_id: uuid.UUID, agent_handle: str) -> asyncio.Lock:
        from app.domain.agent.chat import ChatService

        self.__dict__.setdefault("_seat_locks", {})
        return ChatService._seat_lock_for(
            cast(ChatService, self), topic_id, agent_handle
        )

    async def _resolved_agent(
        self, session: AsyncSession, topic: Topic
    ) -> ResolvedAgent:
        from app.domain.agent.queries import _resolved_agent

        return await _resolved_agent(session, topic)

    async def _agent_at(self, session: AsyncSession, place: Place) -> ResolvedAgent:
        from app.domain.agent.queries import _agent_at

        return await _agent_at(session, place)

    async def _room_of_conversation(
        self, conversation_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID | None]:
        from app.domain.agent.chat import ChatService

        self.__dict__.setdefault("_conversation_rooms", {})
        return await ChatService._room_of_conversation(
            cast(ChatService, self), conversation_id
        )

    async def _acting_handle(
        self, session: AsyncSession, topic_id: uuid.UUID, agent: ResolvedAgent
    ) -> str:
        from app.domain.agent.queries import _acting_handle

        return await _acting_handle(session, topic_id, agent)
