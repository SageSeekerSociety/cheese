"""ChatService 里「这个会话在哪里」的那一半。

一个会话问的是三件事：它落在哪个房间、那个房间里它是哪一个（房间自己、任务、还是
支线，`_room_of_conversation` / `_conversation_place`）；往它里面写一句话写在谁那本
账上（`post_system_event`）；以及它这一席跑出来的会话句柄存在哪一行
（`_save_session_pointer`）。答案按会话缓存，任务换了房间就 `forget_conversation`。

支线那一处的 `thread_replied` 也在这里：一句话落进支线，房间主线上要看得出它长了。

Mixin：方法仍挂在 `ChatService` 上——`tests/support/work_chat.py` 会
`ChatService._room_of_conversation(...)` 地直接取类上的这个属性，`hook_events`
组合时也把 `self._save_session_pointer`、`self._room_of_conversation` 当成服务的
方法收走。
"""

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.domain.agent.harness import harness_for
from app.domain.agent.queries import _agent_at
from app.domain.agent.room_events import post_system_event
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_session.models import AgentSession as _AS
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task.place import PlaceResolver

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.domain.agent.turn.state.live import LiveWork

logger = logging.getLogger(__name__)

#: How many conversations' rooms to remember. Well past the number of
#: conversations one backend hears from at once; a ceiling, not a policy.
_CONVERSATION_ROOMS_KEPT = 2048


class ConversationPlaces:
    """一个会话在哪个房间里、它的会话句柄存在哪——`ChatService` 的又一半。

    属性和方法是服务的，这里声明出来，让类型检查看得见本 mixin 自己的契约。
    """

    if TYPE_CHECKING:
        _sessions: async_sessionmaker
        live: LiveWork

    async def post_system_event(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        *,
        meta: dict | None = None,
    ) -> dict | None:
        """Persist a system event into the conversation ``topic_id`` names —
        a room's own line, or a task's (room_events.py)."""
        room_id, inner_id = await self._room_of_conversation(topic_id)
        return await post_system_event(
            self._sessions,
            room_id,
            content,
            turn_id,
            meta=meta,
            inner_id=inner_id,
        )

    async def _save_session_pointer(
        self,
        topic_id: uuid.UUID,
        session_id: str,
        *,
        agent_handle: str | None = None,
        harness: str | None = None,
    ) -> None:
        """Best-effort: point the PLACE at the (possibly partial) session so the
        next summon resumes it. Never raises — used on failure paths.

        Resolved as a place, not looked up in `topics`: a thread is a `tasks`
        row, so asking that table for one comes back empty and every write below
        is skipped — silently, on the success path as much as the failure one.
        That row is what "this place has run" IS (agent_session/models.py), so a
        thread that skipped it has no 现场 to open after hours of work, and
        nothing for a cold start to resume.
        """
        try:
            async with self._sessions() as session:
                place = await PlaceResolver(session).conversation(topic_id)
                if place is not None:
                    # An event names who ACTED: the seat its session authors
                    # under. The pointer is keyed by the agent that seat
                    # belongs to, the key the next turn reads it back under
                    # (the resume lookup in `TurnPreparation.prepare`); written under
                    # the seat, it is never found and every cold start opens
                    # a new conversation. A room-derived seat, the shared one
                    # and an event that names nobody are the room's agent.
                    owner = await ProjectRepository(session).get(place.project_id)
                    agent = (
                        await AgentInstanceService(session).for_seat_handle(
                            owner, agent_handle
                        )
                        if owner is not None
                        else None
                    ) or await _agent_at(session, place)
                    # 事件没说骨架，就问这个项目跑的是哪个——同一个答法，和开
                    # 这一轮用的那一个（结论 28）。
                    harness = harness or harness_for(owner.settings if owner else None)
                    # Lock the pointer row before moving it: the active-source
                    # guard in `turn.intake.native_entries` holds the same lock
                    # while it validates and mutates, so the two sides of
                    # a session change serialize on the row itself (FB-56 P2-1).

                    await session.execute(
                        select(_AS.id)
                        .where(
                            _AS.conversation_id == place.conversation_id,
                            _AS.agent_handle == agent.handle,
                            _AS.harness == harness,
                        )
                        .with_for_update()
                    )
                    self.live.dead_sessions.discard(
                        (place.conversation_id, agent.handle, session_id)
                    )
                    await AgentSessionService(session).remember(
                        conversation_id=place.conversation_id,
                        agent_handle=agent.handle,
                        resume_token=session_id,
                        harness=harness,
                    )
                    await session.commit()
        except Exception:  # never mask the original failure
            logger.exception("failed to save session pointer for %s", topic_id)

    async def _room_of_conversation(
        self, conversation_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID | None]:
        """``(room, inner)`` for a conversation: a room is its own room with
        nothing inside, a task or a 支线 is the room it is in and itself.

        Everything a session says arrives keyed by its conversation, while the
        roster, the machine and the files are its room's. A task stays in the
        room it hangs in until someone moves it, which drops the remembered
        answer (``forget_conversation``).
        """
        room, inner, _thread = await self._conversation_place(conversation_id)
        return room, inner

    async def _conversation_place(
        self, conversation_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID | None, bool]:
        """``_room_of_conversation``, and whether the conversation is a 支线."""
        known = self.live.conversation_rooms.get(conversation_id)
        if known is not None:
            return known
        async with self._sessions() as session:
            place = await PlaceResolver(session).conversation(conversation_id)
        answer = (
            (place.room_id, place.inner_id, place.thread is not None)
            if place is not None
            else (conversation_id, None, False)
        )
        if len(self.live.conversation_rooms) >= _CONVERSATION_ROOMS_KEPT:
            self.live.conversation_rooms.pop(next(iter(self.live.conversation_rooms)))
        self.live.conversation_rooms[conversation_id] = answer
        return answer

    async def thread_replied(self, conversation_id: uuid.UUID) -> None:
        """A message landed in ``conversation_id``: when that is a 支线, its
        channel's main line shows the 支线 grown."""
        # deferred-import: tests replace this name on app.domain.agent.staleness
        from app.domain.agent.staleness import announce_stale

        room, _inner, thread = await self._conversation_place(conversation_id)
        if thread:
            await announce_stale(room, "threads")

    def forget_conversation(self, conversation_id: uuid.UUID) -> None:
        """A task moved to another room: read its room again next time."""
        self.live.conversation_rooms.pop(conversation_id, None)
