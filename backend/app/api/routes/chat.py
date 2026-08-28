"""Chat WebSocket route.

The WS is a SUBSCRIBER, not the turn's owner (design §4 / v2 R1). A client
message is `submit`ted to the AgentWorkRunner, which runs the turn as a background job
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

The `?token=` is not optional and a socket the connect check refuses is closed
(1008) after one `error` frame carrying `code: auth_required` (no token),
`auth_expired` (a token we could not verify) or `forbidden` (verified, but not a
member of this topic). Those three are the WHOLE refusal set — a client that
recognises only some of them treats the rest as a dropped connection and retries
into a wall, which is the bug the codes exist to prevent.
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
from app.api.deps import get_broker, get_chat_service, get_work_runner
from app.core.config import settings
from app.core.errors import AppError, ForbiddenError
from app.core.obs import get_logger
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.authz.policy import refuse_unauthenticated_chat
from app.domain.identity.actor import Actor

router = APIRouter(tags=["chat"])
_log = get_logger("cheesex.chat_ws")


@router.websocket("/topics/{topic_id}/chat")
async def chat(
    websocket: WebSocket,
    topic_id: uuid.UUID,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
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

    token = websocket.query_params.get("token") or ""
    conn_actor: Actor
    refusal: tuple[str, str] | None = None

    async def relay(queue: asyncio.Queue[dict]) -> None:
        while True:
            await send(await queue.get())

    # Subscribe BEFORE authorising, not after. `accept()` has already returned,
    # so as far as the client is concerned this topic is live and anything it
    # does next may publish here — while the authorisation below is still two
    # or three database round-trips from finishing. Frames published inside that
    # window reach a channel with nobody registered on it, and the ones that are
    # never buffered (a reaction fans out live and is deliberately not retained,
    # so an idle channel cannot look in_flight forever) are simply gone: the
    # person watching sees no emoji appear until something makes them refetch.
    #
    # Registering first costs nothing — frames only queue, and `relay` is not
    # started until authorisation passes, so a refused connection is still sent
    # nothing and drops its queue on the way out.
    async with broker.subscribe(channel, replay=True) as queue:
        # Resolve the actor ONCE from the connection's ?token= (browsers can't
        # set an Authorization header on a WS). A verified token pins authorship
        # for every message on this socket — the client cannot forge `author`.
        # Resolve in a tightly-scoped session (released before the receive loop
        # so it never overlaps the background turn's own sessions on the same
        # connection).
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

        # Snapshot active ids only after registering. If a turn ends while the
        # turn_active frame is in flight, its turn_finished frame is already
        # queued; the client can never be left permanently "working" by that
        # race.
        active_turn_ids = broker.active_turn_ids(channel)
        if active_turn_ids:
            await send({"type": "turn_active", "turn_ids": active_turn_ids})

        relay_task = asyncio.create_task(relay(queue))
        try:
            while True:
                payload = await websocket.receive_json()
                if payload.get("type") != "message":
                    await send({"type": "error", "message": "unsupported message type"})
                    continue
                content = (payload.get("content") or "").strip()
                # Authorship comes from the connection, never from the frame: an
                # authenticated socket is pinned to its verified handle, and the
                # only sockets that reach here without one are local harnesses
                # `chat_ws_allow_anonymous` deliberately admits (config.py).
                author = (
                    conn_actor.handle
                    if conn_actor.authenticated
                    else (payload.get("author") or "anonymous").strip()[:64]
                    or "anonymous"
                )
                # @芝士 toggle: summon the AI, or just post (spec C3).
                summon = bool(payload.get("summon", False))
                # B3: replying to a specific message threads under it.
                reply_to = payload.get("reply_to") or None
                # 图片输入: previously-uploaded worktree files this message carries.
                attachments = [
                    {"path": a["path"], "mime": str(a.get("mime") or "")}
                    for a in (payload.get("attachments") or [])[:9]
                    if isinstance(a, dict)
                    and isinstance(a.get("path"), str)
                    and a["path"]
                ]
                # 乐观渲染的对账号：客户端自己发的这一条叫什么。原样回传，
                # 平台不解释它的内容。
                raw_client_id = payload.get("client_id")
                client_id = (
                    str(raw_client_id)[:64] if isinstance(raw_client_id, str) else None
                )
                if not content and not attachments:
                    await send({"type": "error", "message": "empty content"})
                    continue
                # Await only the short durable receive. Any model work is still
                # background-owned by AgentWorkRunner and survives this socket.
                try:
                    await runner.submit_message(
                        chat_service,
                        topic_id,
                        author=author,
                        content=content,
                        summon=summon,
                        reply_to=reply_to,
                        attachments=attachments,
                        provision_actor=conn_actor,
                        client_id=client_id,
                    )
                except AppError as exc:
                    await send({"type": "error", "message": exc.message})
                except Exception:  # noqa: BLE001 — keep the socket usable
                    _log.exception("chat_message_receive_failed", topic=str(topic_id))
                    await send({"type": "error", "message": "消息未能保存，请重新发送"})
        except WebSocketDisconnect:
            pass
        finally:
            relay_task.cancel()
            with contextlib.suppress(BaseException):
                await relay_task
