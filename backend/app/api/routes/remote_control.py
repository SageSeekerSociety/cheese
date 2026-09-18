"""Native RC worker transport and Cheese's authenticated controller API."""

import asyncio
import json
import time
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    ValidationError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import private_chat
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.harness.claude_code import REMOTE_CONTROLS
from app.domain.agent.remote_control import CONTROLS, store
from app.domain.agent.runtime import get_broker
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(tags=["remote-control"])
# How long the control read waits for the machine's background-task list.
# Everything else in this response comes from the platform's own store; only the
# task list crosses to the machine, and only that part can hang. The page asks
# again a couple of seconds later, so waiting past this buys a task list nobody
# is still waiting for while it holds a request open: on 2026-09-17 a connection
# owner being recreated held this read until it failed, and the page turned each
# failure into a 「后端报错」 line in the room. A normal read of this list takes
# about a tenth of a second.
TASK_LIST_BUDGET_S = 5
# How long a room that hears nothing waits before reading this state anyway. The
# page used to ask every two seconds whether or not anything had happened, which
# was a third of the platform's HTTP requests, nearly all of them answered "still
# nothing". It now hears about a change when the change happens; this is only the
# floor under a frame that was never delivered.
IDLE_CONTROL_REFRESH_S = 30


async def announce(session: dict) -> None:
    """Tell the room its session state moved.

    The platform's own half only: the machine's background-task list is not in
    here, because nothing tells this process when that changes. The panel that
    shows that list keeps reading it; the one in every open room shows the
    agent's questions, which are all in this frame.
    """
    state = await store().snapshot(session)
    await get_broker().publish(
        session["topic_id"], {"type": "agent_control", "state": state}
    )


DbSession = Annotated[AsyncSession, Depends(get_db)]


def launch_claims(request: Request) -> dict:
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    if not claims or not claims.get("p") or not claims.get("t") or not claims.get("rc"):
        raise AuthenticationRequiredError("An RC-enabled place credential is required")
    return claims


async def bootstrap_session(sid: str, request: Request) -> dict:
    claims = launch_claims(request)
    session = await store().get(sid)
    if session["project_id"] != claims["p"] or session["topic_id"] != claims["t"]:
        raise ForbiddenError("RC session belongs to another place")
    return session


async def worker_session(sid: str, request: Request) -> tuple[dict, str]:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    session = await store().authenticate_worker(sid, token)
    return session, token


async def body(request: Request) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16 * 1024 * 1024:
            raise ValidationError("RC event batch exceeds 16 MiB")
    try:
        value = json.loads(raw or b"{}")
    except ValueError as exc:
        raise ValidationError("Invalid RC JSON") from exc
    if not isinstance(value, dict):
        raise ValidationError("RC payload must be an object")
    return value


@router.post("/v1/code/sessions", include_in_schema=False)
async def rc_create(request: Request, db: DbSession) -> dict:
    claims = launch_claims(request)
    place = await TopicService(db).place_or_404(uuid.UUID(claims["t"]))
    if str(place.project_id) != claims["p"]:
        raise ForbiddenError("Place does not belong to this credential")
    if place.room.session_placement and claims.get("r") != str(
        place.room.resource_id or place.room.id
    ):
        raise ConflictError("RC credential belongs to another execution generation")
    data = await body(request)
    # Placement is platform-owned; ignore an execution target supplied by a worker.
    data["execution"] = place.room.session_placement
    session = await store().create(claims, data)
    await announce(session)
    return {"session": session}


@router.post("/v1/code/sessions/{sid}/bridge", include_in_schema=False)
async def rc_bridge(sid: str, request: Request) -> dict:
    rc = store()
    session = await bootstrap_session(sid, request)
    result = await rc.bridge(session)
    await rc.enqueue(
        sid,
        {
            "type": "control_request",
            "request_id": f"cheese-initialize-{result['worker_epoch']}",
            "request": {
                "subtype": "initialize",
                "supportedDialogKinds": ["ask_user_question"],
            },
        },
        "cheese",
    )
    await announce(await rc.get(sid))
    return result


@router.get("/v1/code/sessions/{sid}", include_in_schema=False)
async def rc_session(sid: str, request: Request) -> dict:
    return await bootstrap_session(sid, request)


@router.put("/v1/code/sessions/{sid}", include_in_schema=False)
async def rc_update(sid: str, request: Request) -> dict:
    await bootstrap_session(sid, request)
    data = await body(request)
    return await store().update(
        sid, {k: data[k] for k in ("title", "tags") if k in data}
    )


@router.post("/v1/code/sessions/{sid}/{action}", include_in_schema=False)
async def rc_lifecycle(
    sid: str, action: Literal["archive", "unarchive"], request: Request
) -> dict:
    await bootstrap_session(sid, request)
    await store().update(
        sid, {"status": "archived" if action == "archive" else "active"}
    )
    await announce(await store().get(sid))
    return {}


@router.get("/v1/code/sessions/{sid}/worker", include_in_schema=False)
async def rc_worker_get(sid: str, request: Request) -> dict:
    session, _ = await worker_session(sid, request)
    return {
        "worker": session.get(
            "worker", {"external_metadata": {}, "internal_metadata": {}}
        )
    }


@router.put("/v1/code/sessions/{sid}/worker", include_in_schema=False)
async def rc_worker_put(sid: str, request: Request) -> dict:
    session, _ = await worker_session(sid, request)
    data = await body(request)
    if data.get("worker_epoch") != session["epoch"]:
        raise ConflictError("Stale worker epoch")
    worker = session.get("worker", {}) | {
        k: data[k] for k in ("external_metadata", "internal_metadata") if k in data
    }
    await store().update(
        sid, {"worker": worker, "last_seen": time.time()}, epoch=session["epoch"]
    )
    return {}


@router.get("/v1/code/sessions/{sid}/worker/events/stream", include_in_schema=False)
async def rc_worker_stream(sid: str, request: Request) -> StreamingResponse:
    session, token = await worker_session(sid, request)
    cursor = (
        request.headers.get("last-event-id")
        or request.query_params.get("from_sequence_num")
        or request.query_params.get("last_event_id")
        or "0"
    )
    if not cursor.isdigit():
        raise ValidationError("Invalid RC event cursor")
    return StreamingResponse(
        store().worker_stream(session, token, cursor + "-0"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/v1/code/sessions/{sid}/worker/events", include_in_schema=False)
async def rc_worker_events(sid: str, request: Request) -> dict:
    session, _ = await worker_session(sid, request)
    data = await body(request)
    if data.get("worker_epoch") != session["epoch"]:
        raise ConflictError("Stale worker epoch")
    events = data.get("events", [])
    if (
        not isinstance(events, list)
        or len(events) > 1000
        or any(
            not isinstance(e, dict) or not isinstance(e.get("payload"), dict)
            for e in events
        )
    ):
        raise ValidationError("Invalid RC event batch")
    try:
        await store().receive(sid, events, epoch=data["worker_epoch"])
    except (ValueError, KeyError) as exc:
        raise ValidationError("Invalid RC worker event") from exc
    await announce(session)
    return {}


@router.post("/v1/code/sessions/{sid}/worker/events/delivery", include_in_schema=False)
async def rc_delivery(sid: str, request: Request) -> dict:
    session, _ = await worker_session(sid, request)
    data = await body(request)
    if data.get("worker_epoch") != session["epoch"]:
        raise ConflictError("Stale worker epoch")
    updates = data.get("updates", [])
    if not isinstance(updates, list) or any(
        not isinstance(update, dict)
        or not isinstance(update.get("event_id"), str)
        or update.get("status") not in ("received", "processed")
        for update in updates
    ):
        raise ValidationError("Invalid RC delivery batch")
    await store().delivery(sid, updates, epoch=data["worker_epoch"])
    return {}


@router.post("/v1/code/sessions/{sid}/worker/heartbeat", include_in_schema=False)
async def rc_heartbeat(sid: str, request: Request) -> dict:
    session, _ = await worker_session(sid, request)
    # `connected` is this heartbeat's recency, so a worker that has been quiet
    # long enough to read as gone comes back on this call and nowhere else.
    # Announcing every heartbeat would be a frame a minute saying nothing moved.
    revived = time.time() - session["last_seen"] >= 90
    await store().update(sid, {"last_seen": time.time()}, epoch=session["epoch"])
    if revived:
        await announce(await store().get(sid))
    return {}


@router.post("/v1/code/sessions/{sid}/client/presence", include_in_schema=False)
async def rc_presence(sid: str, request: Request) -> dict:
    await bootstrap_session(sid, request)
    return {}


async def controller(
    topic_id: uuid.UUID, db: AsyncSession, resolver, *, deciding: bool = False
):
    """Whoever is in this room, for reading; not this session's own agent, for
    deciding.

    What must not happen is a session approving its own tool use, answering its
    own question or changing its own permission mode — the shape of "the party
    under review is not the reviewer", which is about stake and not about being
    an agent. A teammate reads the state either way: the page polls the read
    route every few seconds, and refusing an agent there bought nothing.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, project_id=place.project_id, topic_id=place.room_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required to control a session")
    if deciding and actor.is_agent:
        seated = await TopicMemberService(db).resolve_agent_handle(
            topic_id, room_id=place.room_id
        )
        if actor.handle == seated:
            raise ForbiddenError("A session cannot answer its own controls")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    # A control waits for the remote process; do not hold an idle DB transaction.
    await db.commit()
    return actor


@router.get("/topics/{topic_id}/agent/control", operation_id="agent-control-state")
async def control_state(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await controller(topic_id, db, resolver)
    session = await store().current(str(topic_id))
    result = (
        await store().snapshot(session) if session else {"connected": False, "id": None}
    )
    placement = session.get("execution") if session else None
    target = None
    if placement and result["connected"]:
        place = await TopicService(db).place_or_404(topic_id)
        if placement["resource_id"] == str(place.room.resource_id or place.room.id):
            target = placement["execution"]
        await db.commit()
    if session and target:
        # The machine being off does not make the rest of this unknown. The page
        # polls here every few seconds, so raising would paint an error over a
        # room whose state we can read perfectly well — everything but the
        # background tasks, which live on the machine that is not there.
        try:
            async with asyncio.timeout(TASK_LIST_BUDGET_S):
                tasks = await private_chat.control(
                    target, {"subtype": "background_tasks"}
                )
        except DeviceOffline as exc:
            result["device_offline"] = exc.device_id
        except TimeoutError:
            # Not an error the room needs told about: the rest of this response
            # is already correct, and the next poll reads the list again. The
            # owner keeps the call it is running, so giving up on the answer
            # here does not stop the work that produces it.
            result["tasks_unread"] = True
        else:
            result["tasks"].update({task["task_id"]: task for task in tasks["tasks"]})
    return ok(result)


class ControlIn(BaseModel):
    session_id: str = Field(max_length=80)
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=100
    )
    request: dict[str, Any]


async def selected_session(topic_id: uuid.UUID, sid: str) -> dict:
    session = await store().current(str(topic_id))
    if not session or session["id"] != sid or session["status"] != "active":
        raise ConflictError("The active session changed; refresh before controlling it")
    return session


@router.post("/topics/{topic_id}/agent/control", operation_id="agent-control")
async def control(
    topic_id: uuid.UUID,
    data: ControlIn,
    db: DbSession,
    resolver: ActorResolverDep,
    wait: float = Query(default=15, ge=0, le=30),
) -> dict:
    actor = await controller(topic_id, db, resolver, deciding=True)
    session = await selected_session(topic_id, data.session_id)
    if data.request.get("subtype") not in CONTROLS:
        raise ValidationError("Unsupported RC control; see the session's controls list")
    payload = {
        "type": "control_request",
        "request_id": data.request_id,
        "request": data.request,
    }
    placement = session.get("execution")
    target = placement["execution"] if placement else None
    if placement:
        place = await TopicService(db).place_or_404(topic_id)
        if placement["resource_id"] != str(place.room.resource_id or place.room.id):
            raise ConflictError(
                "This session belongs to a retired execution generation"
            )
        await db.commit()
    remote_control = data.request.get("subtype") in REMOTE_CONTROLS
    if data.request.get("subtype") == "stop_task":
        remote_control = str(data.request.get("task_id", "")).startswith("remote-")
    if target and remote_control:
        command = await store().execute_remote(session, payload, actor.handle, target)
    else:
        if target and data.request.get("subtype") == "interrupt":
            await private_chat.control(target, data.request)
        command = await store().enqueue(data.session_id, payload, actor.handle)
    result = await store().result(data.session_id, data.request_id, wait)
    command = await store().command(data.session_id, data.request_id) or command
    return ok(
        {
            "request_id": data.request_id,
            "status": "completed" if result else command["status"],
            "result": result,
        }
    )


@router.get(
    "/topics/{topic_id}/agent/control/{request_id}", operation_id="agent-control-result"
)
async def control_result(
    topic_id: uuid.UUID,
    request_id: str,
    session_id: str,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    await controller(topic_id, db, resolver)
    await selected_session(topic_id, session_id)
    result = await store().result(session_id, request_id)
    command = await store().command(session_id, request_id)
    return ok(
        {
            "result": result,
            "status": "completed"
            if result
            else (command or {}).get("status", "queued"),
        }
    )


class AnswerIn(BaseModel):
    session_id: str = Field(max_length=80)
    request_id: str = Field(min_length=1, max_length=100)
    response: dict[str, Any]


class MessageIn(BaseModel):
    session_id: str = Field(max_length=80)
    uuid: str = Field(
        default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=100
    )
    content: str = Field(min_length=1, max_length=100000)


@router.post("/topics/{topic_id}/agent/message", operation_id="agent-message")
async def control_message(
    topic_id: uuid.UUID, data: MessageIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await controller(topic_id, db, resolver, deciding=True)
    await selected_session(topic_id, data.session_id)
    command = await store().enqueue(
        data.session_id,
        {
            "type": "user",
            "uuid": data.uuid,
            "client_platform": "web_claude_ai",
            "message": {"role": "user", "content": data.content},
        },
        actor.handle,
    )
    return ok({"uuid": data.uuid, "status": command["status"]})


@router.post("/topics/{topic_id}/agent/answer", operation_id="agent-answer")
async def answer(
    topic_id: uuid.UUID, data: AnswerIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await controller(topic_id, db, resolver, deciding=True)
    await selected_session(topic_id, data.session_id)
    rc = store()
    command = await rc.enqueue(
        data.session_id,
        {
            "type": "control_response",
            "uuid": "answer-" + data.request_id,
            "response": {
                "subtype": "success",
                "request_id": data.request_id,
                "response": data.response,
            },
        },
        actor.handle,
    )
    return ok({"status": command["status"]})


@router.get("/topics/{topic_id}/agent/events", operation_id="agent-events")
async def control_events(
    topic_id: uuid.UUID,
    session_id: str,
    db: DbSession,
    resolver: ActorResolverDep,
    cursor: str = Query(default="0-0", pattern=r"^\d+-\d+$"),
) -> dict:
    await controller(topic_id, db, resolver)
    await selected_session(topic_id, session_id)
    return ok({"events": await store().journal(session_id, cursor)})
