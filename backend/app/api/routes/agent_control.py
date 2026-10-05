"""What the room can see of its live session: its state, and what it asks.

The room watches its session and never steers it, so every request here only
reads. The session answers some (its context usage, its MCP servers); the
room's executor answers the rest, because the files live there (read a file,
list the workspace diff). The runtime that holds the room says which are
which (``SessionControls``); this route checks who is asking and sends each
to whoever answers it.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ValidationError,
)
from app.domain.agent import private_chat
from app.domain.agent.chat import ChatService
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.actor import Actor
from app.domain.topic.services import TopicService

router = APIRouter(tags=["agent-control"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
Chat = Annotated[ChatService, Depends(get_chat_service)]


async def controller(topic_id: uuid.UUID, db: AsyncSession, resolver) -> Actor:
    """Whoever is in this room may look at a session here."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        project_id=place.project_id, topic_id=place.conversation_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required to view a session")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    return actor


@router.get("/topics/{topic_id}/agent/control", operation_id="agent-control-state")
async def control_state(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Chat,
    agent: str | None = Query(default=None, max_length=80),
) -> dict:
    """The session of the seat ``agent`` names, or of the room's only one.

    With several teammates live and none named, ``id`` is null and ``seats``
    lists them: the caller names one, and this never picks for it."""
    await controller(topic_id, db, resolver)
    await db.commit()
    runtime = chat.session_controls(topic_id)
    if runtime is None:
        return ok(
            {
                "id": None,
                "connected": False,
                "agent": None,
                "seats": [],
                "controls": [],
                "tasks": {},
            }
        )
    return ok(await runtime.control_state(topic_id, agent=agent))


class ControlIn(BaseModel):
    session_id: str = Field(max_length=80)
    #: The seat whose session ``session_id`` is; omitted, the room's only one.
    agent: str | None = Field(default=None, max_length=80)
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=100
    )
    request: dict[str, Any]


async def executor_target(db: AsyncSession, topic_id: uuid.UUID) -> dict | None:
    """The executor the room's session works on, for this generation of the room.

    Asked of the room's sessions and not of one: they lease the same executor
    today (see `api/routes/execution.py`).
    """
    place = await TopicService(db).place_or_404(topic_id)
    resource = str(place.room.resource_id or place.room.id)
    for held in await AgentSessionService(db).places_in_room(place.room_id):
        if held.lease and held.resource_id == resource:
            return held.lease
    return None


@router.post("/topics/{topic_id}/agent/control", operation_id="agent-control")
async def control(
    topic_id: uuid.UUID,
    data: ControlIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Chat,
) -> dict:
    await controller(topic_id, db, resolver)
    runtime = chat.session_controls(topic_id)
    if runtime is None:
        raise ConflictError("No session is running in this room")
    state = await runtime.control_state(topic_id, agent=data.agent)
    if state.get("id") != data.session_id:
        raise ConflictError("The active session changed; refresh and ask again")
    request = data.request
    if request.get("subtype") not in runtime.controls:
        raise ValidationError("Unsupported control; see the session's controls list")
    target = await executor_target(db, topic_id)
    # A control waits for a remote process; do not hold an idle transaction.
    await db.commit()
    try:
        if request.get("subtype") not in runtime.executor_controls:
            response = await runtime.control(topic_id, request, agent=data.agent)
        elif target is None:
            raise ConflictError("This room has no work machine for that control")
        else:
            response = {
                "subtype": "success",
                "response": await private_chat.control(target, request),
            }
    except ConflictError:
        raise
    except Exception as exc:  # noqa: BLE001 — the room reads why it failed
        response = {"subtype": "error", "error": str(exc) or type(exc).__name__}
    return ok(
        {
            "request_id": data.request_id,
            "status": "completed",
            "result": {"response": {**response, "request_id": data.request_id}},
        }
    )
