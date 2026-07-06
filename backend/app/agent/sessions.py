"""SessionStore: the seam that tracks connector sessions.

A ``Session`` binds a ``session_id``/``session_token`` pair to an
``ActorContext`` and records which viewer (if any) currently holds
"takeover" (command authority), plus lifecycle status. ``SessionStore`` is a
``typing.Protocol`` so a persistent implementation (Redis/DB-backed, to
survive cheesed process restarts and multi-instance backends) can replace
``InMemorySessionStore`` later via ``wiring.py`` without touching callers.
"""

import secrets
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Protocol

from app.agent.context import ActorContext


class SessionStatus(str, Enum):
    """Lifecycle of a connector session (not to be confused with the agent's
    own reported phase -- ``running|idle|prompt|exited`` -- which is relayed
    as opaque status text, see ``proxy.SessionHub.on_agent_text``)."""

    PENDING = "pending"
    ACTIVE = "active"
    ENDED = "ended"


@dataclass
class Session:
    session_id: str
    session_token: str
    actor: ActorContext
    status: SessionStatus = SessionStatus.PENDING
    controller: str | None = None
    """actor/viewer id currently holding takeover; None = orchestrator holds
    command authority (the default, "not taken over" state)."""
    created_at: float = field(default_factory=time.time)


class SessionStore(Protocol):
    """Create/look up/end connector sessions, and mutate the bits of session
    state that change during its life (controller, status)."""

    async def create(self, actor: ActorContext) -> Session: ...

    async def get(self, session_id: str) -> Session | None: ...

    async def get_by_token(self, session_token: str) -> Session | None: ...

    async def list(self) -> list[Session]: ...

    async def end(self, session_id: str) -> None: ...

    async def set_controller(self, session_id: str, controller: str | None) -> Session: ...

    async def set_status(self, session_id: str, status: SessionStatus) -> Session: ...


class InMemorySessionStore:
    """Default ``SessionStore``: process-local dict. Fine for the demo app
    and for single-instance dev/test; swap for a persistent implementation in
    production via ``wiring.py``."""

    def __init__(self) -> None:
        self._by_id: dict[str, Session] = {}
        self._by_token: dict[str, str] = {}

    async def create(self, actor: ActorContext) -> Session:
        session_id = secrets.token_hex(8)
        token = actor.session_token or secrets.token_urlsafe(32)
        bound_actor = replace(actor, session_id=session_id, session_token=token)
        session = Session(
            session_id=session_id,
            session_token=token,
            actor=bound_actor,
            status=SessionStatus.ACTIVE,
        )
        self._by_id[session_id] = session
        self._by_token[token] = session_id
        return session

    async def get(self, session_id: str) -> Session | None:
        return self._by_id.get(session_id)

    async def get_by_token(self, session_token: str) -> Session | None:
        session_id = self._by_token.get(session_token)
        return self._by_id.get(session_id) if session_id is not None else None

    async def list(self) -> list[Session]:
        return list(self._by_id.values())

    async def end(self, session_id: str) -> None:
        session = self._by_id.get(session_id)
        if session is not None:
            session.status = SessionStatus.ENDED

    async def set_controller(self, session_id: str, controller: str | None) -> Session:
        session = self._by_id.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.controller = controller
        return session

    async def set_status(self, session_id: str, status: SessionStatus) -> Session:
        session = self._by_id.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.status = status
        return session
