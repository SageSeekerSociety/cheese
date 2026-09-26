"""The room's controls over its live session: what it shows, and what it sends.

A control is a request the session answers (interrupt it, change its model,
move a running command to the background, stop a task) or one the room's
executor answers, because the files live there (read a file, list the
workspace diff). The runtime that holds the room says which are which
(``SessionControls``); this route checks who is asking and sends each to
whoever answers it.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker, get_chat_service
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    ValidationError,
)
from app.domain.agent import private_chat
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import InProcessBroker
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.actor import Actor
from app.domain.topic.services import TopicService

router = APIRouter(tags=["agent-control"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
Chat = Annotated[ChatService, Depends(get_chat_service)]
Broker = Annotated[InProcessBroker, Depends(get_broker)]


async def controller(topic_id: uuid.UUID, db: AsyncSession, resolver) -> Actor:
    """Whoever is in this room may read and control a session here."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, project_id=place.project_id, topic_id=place.room_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required to control a session")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    return actor


@router.get("/topics/{topic_id}/agent/control", operation_id="agent-control-state")
async def control_state(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep, chat: Chat
) -> dict:
    await controller(topic_id, db, resolver)
    await db.commit()
    runtime = chat.session_controls(topic_id)
    if runtime is None:
        return ok({"id": None, "connected": False, "controls": [], "tasks": {}})
    return ok(await runtime.control_state(topic_id))


class ControlIn(BaseModel):
    session_id: str = Field(max_length=80)
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=100
    )
    request: dict[str, Any]


def not_its_own(state: dict, actor: Actor) -> None:
    """A session may not decide its own controls.

    Raising its own permission mode, or stopping the thing it is being watched
    doing, is the party under review acting as the reviewer. The question is
    whether this actor IS this session — asked of the session, which knows,
    and never of the room, which may seat several agents.
    """
    if actor.handle == state.get("agent_handle"):
        raise ForbiddenError("A session cannot decide its own controls")


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


async def say_who_stopped(
    chat: ChatService,
    broker: InProcessBroker,
    topic_id: uuid.UUID,
    work: uuid.UUID,
    actor: Actor,
) -> None:
    """A stopped run is a line in the room, under the run it ended: a turn
    that just goes quiet reads the same as one that is still stuck."""
    block = await chat.post_system_event(
        topic_id, f"<@{actor.handle}> 停止了这次运行", work
    )
    if block is not None:
        await broker.publish(str(topic_id), {"type": "event_block", "block": block})


@router.post("/topics/{topic_id}/agent/control", operation_id="agent-control")
async def control(
    topic_id: uuid.UUID,
    data: ControlIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Chat,
    broker: Broker,
) -> dict:
    actor = await controller(topic_id, db, resolver)
    runtime = chat.session_controls(topic_id)
    if runtime is None:
        raise ConflictError("No session is running in this room")
    state = await runtime.control_state(topic_id)
    if state.get("id") != data.session_id:
        raise ConflictError("The active session changed; refresh before controlling it")
    not_its_own(state, actor)
    request = data.request
    if request.get("subtype") not in runtime.controls:
        raise ValidationError("Unsupported control; see the session's controls list")
    target = await executor_target(db, topic_id)
    # A control waits for a remote process; do not hold an idle transaction.
    await db.commit()
    try:
        if request.get("subtype") not in runtime.executor_controls:
            response = await runtime.control(topic_id, request)
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
    if stopped := response.pop("work_id", None):
        await say_who_stopped(chat, broker, topic_id, uuid.UUID(stopped), actor)
    return ok(
        {
            "request_id": data.request_id,
            "status": "completed",
            "result": {"response": {**response, "request_id": data.request_id}},
        }
    )
