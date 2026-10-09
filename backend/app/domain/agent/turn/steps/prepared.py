"""The narrow, fully prepared contract for an actual session send."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.domain.agent.harness import SessionRef
from app.domain.agent.session_host.contract import Image
from app.domain.delivery.input_identity import InputRegistrar
from app.domain.memory.scopes import MemoryScope


class SessionSender(Protocol):
    async def send(
        self,
        session: SessionRef,
        message: str,
        *,
        system_prompt: str,
        work_id: uuid.UUID,
        on_mark: Callable[[uuid.UUID], None],
        register_input: InputRegistrar,
        resume_token: str | None = None,
        model: str | None = None,
        env: dict[str, str] | None = None,
        acting: str | None = None,
        needs_place: bool = True,
        keeps_nothing: bool = False,
        images: list[Image] | None = None,
        owes_reply: bool = False,
        session_opening: str = "",
        opening_changes: str = "",
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class PreparedSend:
    project_id: uuid.UUID
    room_id: uuid.UUID
    inner_id: uuid.UUID | None
    acting_agent: str
    agent_handle: str
    agent_pool: tuple[MemoryScope, str] | None
    roster: list[dict]
    topic_refs: list[dict]
    consumed_ids: list[uuid.UUID]
    turn_images: list[Image]
    replay_notice: str | None
    resume_session_id: str | None
    harness: str
    needs_place: bool
    keeps_nothing: bool
    sender: SessionSender
    activate: Callable[[SessionRef], Awaitable[None]]
