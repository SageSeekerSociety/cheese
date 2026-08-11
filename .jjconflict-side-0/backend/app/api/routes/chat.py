"""Chat WebSocket route.

The WS is a SUBSCRIBER, not the turn's owner (design §4 / v2 R1). A client
message is `submit`ted to the TurnRunner, which runs the turn as a background job
and publishes its frames to the Broker; this connection relays whatever frames
land on the topic channel. So a disconnect only drops the subscription — the turn
keeps running and persisting (invariant 2: the job doesn't depend on who watches),
and multiple connections to the same topic all see the live stream.

Protocol (unchanged frontend contract):
  connect → /api/topics/{id}/chat?token=<session token>   (required)
  client → {"type":"message","content": str, "summon": bool,
            "attachments"?: [{"path": str, "mime": str}]}
  server → user_block / reaction / tool / todo / state / event_block /
           assistant_block / error / done
(No token streaming: 芝士 speaks in discrete assistant_block messages — one per
completed SDK AssistantMessage — Slack-style.)

The `?token=` is not optional and a socket that fails to authenticate is closed
(1008) after one `error` frame carrying `code: auth_expired | auth_required`.
A legacy `author` field is accepted and IGNORED — the author is the connection's
verified handle. Both halves are the same lesson: this socket used to admit
anyone and take their word for who they were, so an expired token turned a
user's messages into 匿名者 posts while their client kept reporting success.

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
from app.core.config import settings
from app.core.errors import ForbiddenError
from app.core.obs import get_logger
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import InProcessBroker, TurnRunner
from app.domain.authz.policy import refuse_unauthenticated_chat
from app.domain.identity.actor import Actor

router = APIRouter(tags=["chat"])
_log = get_logger("cheesex.chat_ws")


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
    # message on this socket — the client cannot forge `author`.
    # Resolve in a tightly-scoped session (released before the receive loop so it
    # never overlaps the background turn's own sessions on the same connection).
    token = websocket.query_params.get("token") or ""
    conn_actor: Actor
    refusal: tuple[str, str] | None = None
    async with chat_service.session_factory() as auth_session:
        resolver = ActorResolver(
            session=auth_session, bearer=token or None, cheese_token=""
        )
        conn_actor = await resolver.resolve(fallback_handle=None, topic_id=topic_id)
        refusal = refuse_unauthenticated_chat(
            conn_actor,
            token_presented=bool(token),
            allow_anonymous=settings.chat_ws_allow_anonymous,
        )
        if refusal is None and conn_actor.authenticated:
            project_id = await resolver.project_of_topic(topic_id)
            if project_id is not None:
                try:
                    await resolver.authorize_topic(
                        conn_actor, project_id=project_id, topic_id=topic_id
                    )
                except ForbiddenError as exc:
                    refusal = ("forbidden", exc.args[0])
    if refusal is not None:
        code, message = refusal
        _log.info("chat_ws_refused", code=code, topic=str(topic_id))
        await send({"type": "error", "code": code, "message": message})
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
            # Authorship comes from the connection, never from the frame: an
            # authenticated socket is pinned to its verified handle, and the only
            # sockets that reach here without one are the local harnesses
            # `chat_ws_allow_anonymous` deliberately admits (config.py). Trimmed
            # and capped so that legacy path cannot stuff control chars or an
            # unbounded string into a stored block — hygiene, not authenticity.
            author = (
                conn_actor.handle
                if conn_actor.authenticated
                else (payload.get("author") or "anonymous").strip()[:64] or "anonymous"
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
