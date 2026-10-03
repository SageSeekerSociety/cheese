"""Chat WebSocket route: the room's live feed. Nothing is written through it.

A message enters a room the same way for everyone, people and agents alike:
`POST /topics/{id}/messages` (`topics_messages.py`). This socket only carries
what lands: the frames the broker publishes on the room's channel. So a
disconnect only drops the subscription — every turn keeps running and
persisting (the job doesn't depend on who watches), and several connections
to the same topic all see the same stream.

Protocol:
  connect → /api/topics/{id}/chat?token=<session token>   (required)
  client → {"type":"ping"}  →  server → {"type":"pong"}
  client → {"type":"typing"} / {"type":"typing","active":false}
  server → user_block / reaction / tool / todo / state / event_block /
           assistant_block / live / activity / activity_snapshot / error / done
(Typing is the one thing a client says on this socket, and it is not written:
it is member activity — who is busy in this room right now, a person composing
or an agent with a turn running (`agent/activity.py`) — and it lives only in the
broker. The member is the socket's credential, never a field of the frame.)
(The ping is the browser's liveness probe. A socket can sit OPEN for minutes
after its path stopped carrying frames — the browser only learns when TCP
gives up — so the client asks every few seconds and replaces the socket when
no answer comes; the reply is the whole point, it carries nothing.)
(A chat publication arrives whole, as an assistant_block message; while an
agent is still writing one, `live` frames carry what it has written so far,
unstored, and the block replaces them (`live_frames.py`). Terminal output
arrives as activity event_block records.)

The `?token=` is not optional and a socket the connect check refuses is closed
(1008) after one `error` frame carrying `code: auth_required` (no token),
`auth_expired` (a token we could not verify) or `forbidden` (verified, but not a
member of this topic). Those three are the WHOLE refusal set — a client that
recognises only some of them treats the rest as a dropped connection and retries
into a wall, which is the bug the codes exist to prevent.
"""

import asyncio
import contextlib
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.api.auth import ActorResolver
from app.api.deps import get_broker, get_chat_service
from app.core.errors import ForbiddenError
from app.core.obs import get_logger
from app.core.sentences import error_frame
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import InProcessBroker
from app.domain.agent.turn_adoption import adopt, open_turns_on
from app.domain.authz.policy import refuse_unauthenticated_chat
from app.domain.room_task.services import TaskService

router = APIRouter(tags=["chat"])
_log = get_logger("cheesex.chat_ws")


@router.websocket("/topics/{topic_id}/chat")
async def chat(
    websocket: WebSocket,
    topic_id: uuid.UUID,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    broker: Annotated[InProcessBroker, Depends(get_broker)],
) -> None:
    channel = str(topic_id)
    # One lock so the relay task and the receive loop never send concurrently
    # (Starlette WebSockets are not safe for concurrent sends).
    send_lock = asyncio.Lock()

    async def send(frame: dict) -> None:
        async with send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await websocket.send_json(frame)
                if frame.get("type") in {"user_block", "assistant_block"}:
                    _log.info(
                        "chat_message_frame_sent",
                        topic=str(topic_id),
                        frame_type=frame["type"],
                        block_id=(frame.get("block") or {}).get("id"),
                        sent_unix_ms=time.time() * 1000,
                    )

    token = websocket.query_params.get("token") or ""
    refusal: tuple[str, str] | None = None
    open_turns: list[tuple[str, float, str | None]] = []

    async def relay(queue: asyncio.Queue[dict]) -> None:
        while True:
            await send(await queue.get())

    # Subscribe first, authorise next, accept last. Once the client sees the
    # socket open it treats the topic as live: it posts a message, reacts, opens
    # a card — and each of those publishes on this channel at once, from another
    # request. Frames published into a channel with nobody registered on it are
    # gone (a reaction fans out live and is deliberately not retained, and the
    # echo of a message the client just posted would be lost the same way). In
    # this order "the socket is open" already means "this socket hears
    # everything from here on, and is allowed to".
    #
    # Registering first costs nothing — frames only queue, and `relay` is not
    # started until authorisation passes, so a refused connection is sent
    # nothing but its refusal and drops its queue on the way out.
    async with broker.subscribe(channel, replay=True) as queue:
        # Browsers can't set an Authorization header on a WS, so the
        # connection's ?token= is the credential. Resolve in a tightly-scoped
        # session, released before the receive loop.
        async with chat_service.session_factory() as auth_session:
            resolver = ActorResolver(
                session=auth_session, bearer=token or None, cheese_token=""
            )
            conn_actor = await resolver.resolve(topic_id=topic_id)
            refusal = refuse_unauthenticated_chat(
                conn_actor, token_presented=bool(token)
            )
            if refusal is None:
                # A card is not a room, but it has a channel of its own: its
                # 分身's events and checklist go out on the card id (chat.py,
                # `todo_write`). Whoever may watch it is whoever may enter its
                # room, found through the card; otherwise an outsider holding
                # the id from a `?card=` link finds no room and is let in.
                card = await TaskService(auth_session).get(topic_id)
                room_id = card.room_id if card is not None else topic_id
                project_id = await resolver.project_of_topic(room_id)
                if project_id is not None:
                    try:
                        await resolver.authorize_topic(
                            conn_actor, project_id=project_id, topic_id=room_id
                        )
                    except ForbiddenError as exc:
                        refusal = ("forbidden", exc.args[0])
                if refusal is None:
                    # The turns still running here, as the database has them.
                    # The broker's own list lives in this process only, and a
                    # deploy replaces the process (twice: the next container,
                    # then the recreated one), so a turn started before it, or
                    # on the other container while both ran, is missing from it
                    # though its agent is still at work.
                    open_turns = await open_turns_on(
                        auth_session,
                        room_id,
                        task_id=card.id if card is not None else None,
                    )
        if refusal is not None:
            code, message = refusal
            _log.info("chat_ws_refused", code=code, topic=str(topic_id))
            await websocket.accept()
            await send(error_frame(message, type="error", code=code))
            await websocket.close(code=1008)
            return

        # Snapshot active ids after registering and before accepting. A turn
        # that starts after this has its turn_started queued; one that ends
        # while the turn_active frame is in flight has its turn_finished
        # queued — the client can never be left permanently "working".
        adopt(broker, channel, open_turns)
        active_turn_ids = broker.active_turn_ids(channel)
        await websocket.accept()
        if active_turn_ids:
            await send(
                {
                    "type": "turn_active",
                    "turn_ids": active_turn_ids,
                    "since": broker.active_turns_since(channel),
                    "agents": broker.activity.turn_agents(channel),
                }
            )
        # Who is busy here now, when anyone is (like turn_active: a client
        # starts every connection from nobody). Activity frames already queued
        # are applied after it; every change emits one, in order, so the client
        # ends on the current state whichever side of the snapshot a change fell.
        if busy := broker.activity.snapshot(channel):
            await send({"type": "activity_snapshot", "members": busy})

        relay_task = asyncio.create_task(relay(queue))
        try:
            # A send that finds the peer gone closes the socket on our side and
            # is swallowed by `send` above, so nothing raises: the next read is
            # what notices — and a read on a socket already closed is answered
            # with a RuntimeError, not a disconnect. That is uvicorn's
            # 「Exception in ASGI application」 for a browser that merely went
            # away (three on 2026-09-18, each one an alert); the state is the
            # fact to check, and it ends the loop the way a disconnect does.
            while websocket.application_state == WebSocketState.CONNECTED:
                payload = await websocket.receive_json()
                if payload.get("type") == "ping":
                    await send({"type": "pong"})
                    continue
                if payload.get("type") == "typing":
                    await broker.typing(
                        channel,
                        conn_actor.handle,
                        active=payload.get("active") is not False,
                    )
                    continue
                # A message is POSTed to /topics/{id}/messages; this socket
                # writes nothing, so it says so rather than dropping the frame.
                await send({"type": "error", "message": "unsupported message type"})
        except WebSocketDisconnect:
            pass
        finally:
            relay_task.cancel()
            with contextlib.suppress(BaseException):
                await relay_task
