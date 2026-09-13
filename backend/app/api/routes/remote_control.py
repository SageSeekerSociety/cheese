"""Native RC worker transport and Cheese's authenticated controller API."""

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
from app.domain.agent.harness.claude_code import REMOTE_CONTROLS
from app.domain.agent.remote_control import CONTROLS, store
from app.domain.topic.services import TopicService

router = APIRouter(tags=["remote-control"])
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
    return {"session": await store().create(claims, data)}


@router.post("/v1/code/sessions/{sid}/bridge", include_in_schema=False)
async def rc_bridge(sid: str, request: Request) -> dict:
    rc = store()
    result = await rc.bridge(await bootstrap_session(sid, request))
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
    await store().update(sid, {"last_seen": time.time()}, epoch=session["epoch"])
    return {}


@router.post("/v1/code/sessions/{sid}/client/presence", include_in_schema=False)
async def rc_presence(sid: str, request: Request) -> dict:
    await bootstrap_session(sid, request)
    return {}


async def controller(topic_id: uuid.UUID, db: AsyncSession, resolver):
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, project_id=place.project_id, topic_id=place.room_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required to control a session")
    if actor.is_agent:
        raise ForbiddenError("Session controls require a human identity")
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
        tasks = await private_chat.control(target, {"subtype": "background_tasks"})
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
    actor = await controller(topic_id, db, resolver)
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
    actor = await controller(topic_id, db, resolver)
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
    actor = await controller(topic_id, db, resolver)
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
