"""Chat WebSocket route.

The WS is a SUBSCRIBER, not the turn's owner (design §4 / v2 R1). A client
message is `submit`ted to the TurnRunner, which runs the turn as a background job
and publishes its frames to the Broker; this connection relays whatever frames
land on the topic channel. So a disconnect only drops the subscription — the turn
keeps running and persisting (invariant 2: the job doesn't depend on who watches),
and multiple connections to the same topic all see the live stream.

Protocol (unchanged frontend contract):
  client → {"type":"message","content": str, "author": str, "summon": bool,
            "attachments"?: [{"path": str, "mime": str}]}
  server → user_block / reaction / tool / todo / state / event_block /
           assistant_block / error / done
(No token streaming: 芝士 speaks in discrete assistant_block messages — one per
completed SDK AssistantMessage — Slack-style.)

Attachments are uploaded FIRST via POST /api/topics/{id}/attachments (the file
lands in the topic's worktree); the WS message then references them by path —
the frame itself stays JSON text, no binary over the socket.
"""

import asyncio
import contextlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.auth import ActorResolver
from app.api.deps import get_broker, get_chat_service, get_turn_runner
from app.core.errors import ForbiddenError
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import InProcessBroker, TurnRunner
from app.domain.identity.actor import Actor

router = APIRouter(tags=["chat"])


@router.websocket("/api/topics/{topic_id}/chat")
async def chat(
    websocket: WebSocket,
    topic_id: uuid.UUID,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[TurnRunner, Depends(get_turn_runner)],
    broker: Annotated[InProcessBroker, Depends(get_broker)],
) -> None:
    await websocket.accept()
    channel = str(topic_id)
    # One lock so the relay task and the receive loop never send concurrently
    # (Starlette WebSockets are not safe for concurrent sends).
    send_lock = asyncio.Lock()

    async def send(frame: dict) -> None:
        async with send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await websocket.send_json(frame)

    # Resolve the actor ONCE from the connection's ?token= (browsers can't set an
    # Authorization header on a WS). A verified token pins authorship for every
    # message on this socket — the client can no longer forge `author`. No token →
    # anonymous, and the per-message `author` fallback (Phase-0) applies.
    # Resolve in a tightly-scoped session (released before the receive loop so it
    # never overlaps the background turn's own sessions on the same connection).
    conn_actor: Actor
    denied_message = "无权访问"
    async with chat_service.session_factory() as auth_session:
        resolver = ActorResolver(
            session=auth_session,
            bearer=websocket.query_params.get("token"),
            cheese_token="",
        )
        conn_actor = await resolver.resolve(fallback_handle=None, topic_id=topic_id)
        denied = False
        if conn_actor.authenticated:
            project_id = await resolver.project_of_topic(topic_id)
            if project_id is not None:
                try:
                    await resolver.authorize_topic(
                        conn_actor, project_id=project_id, topic_id=topic_id
                    )
                except ForbiddenError as exc:
                    denied_message = exc.args[0]
                    denied = True
    if denied:
        await send({"type": "error", "message": denied_message})
        await websocket.close(code=1008)
        return

    async def relay() -> None:
        # Subscribe BEFORE the first submit so no frame is missed (R10). replay=True
        # catches up the in-progress turn on a mid-turn (re)connect (R3); between
        # turns the buffer is empty, so a fresh connection replays nothing.
        async with broker.subscribe(channel, replay=True) as queue:
            while True:
                await send(await queue.get())

    # A turn already mid-stream? Tell the client BEFORE the replay starts, so
    # re-entering a topic during the silent thinking phase (no deltas yet)
    # still shows 正在思考 instead of nothing.
    if broker.in_flight(channel):
        await send({"type": "turn_active"})

    relay_task = asyncio.create_task(relay())
    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "message":
                await send({"type": "error", "message": "unsupported message type"})
                continue
            content = (payload.get("content") or "").strip()
            # Verified token → the actor's handle (forgery-proof); else Phase-0
            # fallback to the body's author (deprecated, works pre-token).
            author = (
                conn_actor.handle
                if conn_actor.authenticated
                else (payload.get("author") or "anonymous")
            )
            # @芝士 toggle: summon the AI, or just post (spec C3, default post).
            summon = bool(payload.get("summon", False))
            # B3: replying to a specific message threads under it.
            reply_to = payload.get("reply_to") or None
            # 图片输入: previously-uploaded worktree files this message carries.
            # Structural validation only; the path was produced by the upload
            # route, and block creation re-checks nothing content-wise.
            attachments = [
                {"path": a["path"], "mime": str(a.get("mime") or "")}
                for a in (payload.get("attachments") or [])[:9]
                if isinstance(a, dict) and isinstance(a.get("path"), str) and a["path"]
            ]
            if not content and not attachments:
                await send({"type": "error", "message": "empty content"})
                continue
            # Fire-and-forget: the turn runs in the background and streams back
            # over the broker; this loop stays free to accept more messages.
            runner.submit(
                chat_service,
                topic_id,
                author=author,
                content=content,
                summon=summon,
                reply_to=reply_to,
                attachments=attachments,
            )
    except WebSocketDisconnect:
        pass
    finally:
        relay_task.cancel()
        with contextlib.suppress(BaseException):
            await relay_task
