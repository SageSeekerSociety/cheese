"""The rooms WebSocket: one connection per page, carrying every room it watches.

A message enters a room the same way for everyone, people and agents alike:
`POST /topics/{id}/messages` (`topics_messages.py`). This socket only carries
what lands: the frames the broker publishes on each room's channel. So a
disconnect only drops the subscriptions — every turn keeps running and
persisting (the job doesn't depend on who watches), and several connections
watching the same room all see the same stream.

One connection for every room a page shows, rather than one per room: moving
to another channel used to tear a socket down and open the next one — a new
TCP and TLS handshake to the edge, a tunnel round trip, authentication — and
the room read as 未连接 until it finished. Now the move is two frames on a
link that is already up.

Protocol:
  connect → /api/rooms/live?token=<session token>   (required)
  client → {"type":"subscribe","topic":id,"token":<session token>}
         ← {"type":"subscribed","topic":id,"newest":<block id>|null,
            "room":<snapshot>|null}, then that room's frames
  client → {"type":"unsubscribe","topic":id}
  client → {"type":"ping","topic":id}  →  server → {"type":"pong","topic":id}
  client → {"type":"typing","topic":id} / {..., "active":false}
  client → {"type":"sync","topic":id}  →  server → {"type":"room_state", ...}
  server → user_block / reaction / tool / todo / state / event_block /
           assistant_block / live / activity / activity_snapshot / error / done
Every frame the server sends about a room carries that room's id as `topic`.
(Typing is the one thing a client says here, and it is not written: it is
member activity — who is busy in this room right now, a person composing or an
agent with a turn running (`agent/realtime/activity.py`) — and it lives only in
the broker. The member is the subscription's credential, never a frame field.)
(`sync` asks for the room's live state as one frame, empty or not: the turns
running here and who is busy. The opening `turn_active` / `activity_snapshot`
are sent only when something is going on, so a client cannot tell "nobody is
busy" from "not told yet" by them. A client that resubscribes keeps what it was
showing and asks; the answer is what it reconciles against.)
(`newest` is the newest block the room shows once the subscription is
registered — what its pages read, `GET /topics/{id}/blocks?shown=true`.
A client reads the room's history over HTTP, and a block stored after that
read but before the subscription was registered reaches it by neither path; a
client that does not hold `newest` reads the room's tail again. Anything stored
after `newest` is published to this subscription.)
(`room` is the room as it stood once the subscription was registered — its
roster, tasks, pins, threads, proposal cards; a task's own row, origin and
review comments — each piece shaped like the route that serves it
(`room_snapshot.py`). Like `newest`, it is a line: anything that changes after
it is published to this subscription. Null when it could not be read; the
client then reads the pieces itself.)
(The ping is the browser's liveness probe: a socket can sit OPEN for minutes
after its path stopped carrying frames, so the client asks every few seconds
and replaces the link when no answer comes.)
(A chat publication arrives whole, as an assistant_block message; while an
agent is still writing one, `live` frames carry what it has written so far,
unstored, and the block replaces them (`live_frames.py`). Terminal output
arrives as activity event_block records.)

Each subscription authenticates with the token in its own frame. The page's
token is refreshed while the link stays up, and a room opened an hour in must be
judged by the credential the page holds now, not the one it connected with. A
refused subscription gets one `error` frame carrying `code: auth_required` (no
token), `auth_expired` (a token we could not verify) or `forbidden` (verified,
but not a member of this room), then `{"type":"closed","topic":id}`. Those
three are the WHOLE refusal set — a client that recognises only some of them
treats the rest as a dropped room and retries into a wall, which is the bug the
codes exist to prevent. The connection itself needs a token that verifies; one
that does not is told why (same codes, no `topic`) and closed with 1008.

A room can also be dropped for reading too slowly: one that lets more than
`MAX_SUBSCRIBER_BYTES` of frames pile up unread is ended with
`{"type":"closed","topic":id,"code":1013}` instead of being allowed to grow the
process's memory. The other rooms on the connection are untouched, and
resubscribing — what the client does for any drop — refetches history and
resumes from the replay buffer.
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
from app.api.room_snapshot import room_snapshot
from app.core.errors import ForbiddenError
from app.core.obs import get_logger
from app.core.sentences import error_frame
from app.domain.agent.chat import ChatService
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.realtime.subscriber_queue import (
    SubscriberOverflow,
    SubscriberQueue,
)
from app.domain.agent.turn_adoption import adopt, open_turns_on, watch_books
from app.domain.authz.policy import refuse_unauthenticated_chat
from app.domain.block.queries import newest_block_id
from app.domain.room_task.place import PlaceResolver
from app.domain.room_task.services import TaskService

router = APIRouter(tags=["chat"])
_log = get_logger("cheesex.chat_ws")


@router.websocket("/rooms/live")
async def rooms_live(
    websocket: WebSocket,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    broker: Annotated[InProcessBroker, Depends(get_broker)],
) -> None:
    # One lock so the rooms' relay tasks and the receive loop never send
    # concurrently (Starlette WebSockets are not safe for concurrent sends).
    send_lock = asyncio.Lock()

    async def send(frame: dict) -> None:
        async with send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await websocket.send_json(frame)
                if frame.get("type") in {"user_block", "assistant_block"}:
                    _log.info(
                        "chat_message_frame_sent",
                        topic=frame.get("topic"),
                        frame_type=frame["type"],
                        block_id=(frame.get("block") or {}).get("id"),
                        sent_unix_ms=time.time() * 1000,
                    )

    token = websocket.query_params.get("token") or ""
    async with chat_service.session_factory() as auth_session:
        actor = await ActorResolver(
            session=auth_session, bearer=token or None, cheese_token=""
        ).resolve()
    if refusal := refuse_unauthenticated_chat(actor, token_presented=bool(token)):
        code, message = refusal
        _log.info("chat_ws_refused", code=code)
        await websocket.accept()
        await send(error_frame(message, type="error", code=code))
        await websocket.close(code=1008)
        return

    await websocket.accept()
    rooms: dict[uuid.UUID, _Room] = {}

    def forget(room: "_Room") -> None:
        """A room whose feed ended on its own (refused, overflowed) leaves the
        table — unless a newer subscription to it has already taken its place."""
        if rooms.get(room.topic_id) is room:
            del rooms[room.topic_id]

    def drop(topic_id: uuid.UUID) -> None:
        room = rooms.pop(topic_id, None)
        if room is not None:
            room.task.cancel()

    try:
        # A send that finds the peer gone closes the socket on our side and is
        # swallowed by `send` above, so nothing raises: the next read is what
        # notices — and a read on a socket already closed is answered with a
        # RuntimeError, not a disconnect. The state is the fact to check, and it
        # ends the loop the way a disconnect does.
        while websocket.application_state == WebSocketState.CONNECTED:
            payload = await websocket.receive_json()
            kind = payload.get("type") if isinstance(payload, dict) else None
            try:
                topic_id = uuid.UUID(str(payload.get("topic")))
            except (AttributeError, ValueError):
                await send({"type": "error", "message": "a room frame names its topic"})
                continue
            topic = str(topic_id)
            if kind == "subscribe":
                drop(topic_id)
                room = _Room(topic_id, str(payload.get("token") or ""))
                room.task = asyncio.create_task(
                    room.watch(chat_service, broker, send, forget),
                    name=f"room feed {topic}",
                )
                rooms[topic_id] = room
                continue
            if kind == "unsubscribe":
                drop(topic_id)
                continue
            if kind == "ping":
                await send({"type": "pong", "topic": topic})
                continue
            room = rooms.get(topic_id)
            if room is None or room.handle is None:
                # Not (yet) watching this room: nothing to sync or type into.
                continue
            if kind == "sync":
                await send(
                    {
                        "type": "room_state",
                        "topic": topic,
                        "turn_ids": broker.active_turn_ids(topic),
                        "since": broker.active_turns_since(topic),
                        "agents": broker.activity.turn_agents(topic),
                        "members": broker.activity.snapshot(topic),
                    }
                )
                continue
            if kind == "typing":
                typing = payload.get("active") is not False
                await broker.typing(topic, room.handle, active=typing)
                if typing and room.room_id is not None:
                    chat_service.prewarm.room_active(room.room_id)
                continue
            # A message is POSTed to /topics/{id}/messages; this socket writes
            # nothing, so it says so rather than dropping the frame.
            await send(
                {"type": "error", "topic": topic, "message": "unsupported message type"}
            )
    except WebSocketDisconnect:
        pass
    finally:
        tasks = [room.task for room in rooms.values()]
        rooms.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task


class _Room:
    """One room watched on a connection: its feed, and who is watching."""

    def __init__(self, topic_id: uuid.UUID, token: str) -> None:
        self.topic_id = topic_id
        self.token = token
        self.task: asyncio.Task[None]
        #: The member watching, once the subscription is authorised.
        self.handle: str | None = None
        #: The room to keep warm while it is watched; None for a task's channel.
        self.room_id: uuid.UUID | None = None

    async def watch(self, chat_service, broker, send, forget) -> None:
        """Feed this room to the connection until it is dropped or ends."""
        topic = str(self.topic_id)

        async def tagged(frame: dict) -> None:
            await send({**frame, "topic": topic})

        try:
            # Subscribe first, authorise next, acknowledge last. Once the client
            # sees `subscribed` it treats the room as live: it posts a message,
            # reacts, opens a card — and each of those publishes on this channel
            # at once, from another request. Frames published into a channel with
            # nobody registered on it are gone (a reaction fans out live and is
            # deliberately not retained, and the echo of a message the client
            # just posted would be lost the same way). In this order "subscribed"
            # already means "this connection hears everything from here on, and
            # is allowed to".
            async with broker.subscribe(topic, replay=True) as queue:
                refusal, open_turns, card, newest, room = await self._authorise(
                    chat_service
                )
                if refusal is not None:
                    code, message = refusal
                    _log.info("chat_ws_refused", code=code, topic=topic)
                    await tagged(error_frame(message, type="error", code=code))
                    await tagged({"type": "closed"})
                    return
                # Snapshot active ids after registering and before acknowledging.
                # A turn that starts after this has its turn_started queued; one
                # that ends while the turn_active frame is in flight has its
                # turn_finished queued — the client is never left "working".
                adopt(broker, topic, open_turns)
                active_turn_ids = broker.active_turn_ids(topic)
                await tagged({"type": "subscribed", "newest": newest, "room": room})
                if active_turn_ids:
                    await tagged(
                        {
                            "type": "turn_active",
                            "turn_ids": active_turn_ids,
                            "since": broker.active_turns_since(topic),
                            "agents": broker.activity.turn_agents(topic),
                        }
                    )
                # Who is busy here now, when anyone is (like turn_active: a client
                # starts every subscription from nobody).
                if busy := broker.activity.snapshot(topic):
                    await tagged({"type": "activity_snapshot", "members": busy})
                # Somebody is here, and a message may follow: a session its
                # runner let go while the room sat idle starts now rather than
                # when it arrives.
                if card is None and self.room_id is not None:
                    chat_service.prewarm.room_active(self.room_id)
                # The adoption above is a snapshot, and a turn that ends on the
                # OTHER container of an overlapping rollout does not send its
                # `turn_finished` here: re-read the room's books while it is
                # watched (`turn_adoption.watch_books`).
                books = asyncio.create_task(
                    watch_books(
                        chat_service.session_factory, broker, topic, self.topic_id
                    ),
                    name=f"turn books {topic}",
                )
                try:
                    await self._relay(queue, tagged)
                finally:
                    books.cancel()
                    with contextlib.suppress(BaseException):
                        await books
        finally:
            forget(self)

    async def _authorise(self, chat_service):
        """(refusal, open turns, task card, newest block id, room snapshot) for
        this subscription's credential. Called after the subscription is
        registered, so what it reads is a line: the newest block and the room as
        the snapshot has it, with everything after them published to this
        subscription."""
        async with chat_service.session_factory() as auth_session:
            resolver = ActorResolver(
                session=auth_session, bearer=self.token or None, cheese_token=""
            )
            actor = await resolver.resolve(topic_id=self.topic_id)
            refusal = refuse_unauthenticated_chat(
                actor, token_presented=bool(self.token)
            )
            if refusal is not None:
                return refusal, [], None, None, None
            # A task is a conversation of its own, on a channel of its own:
            # everything its turns publish goes out on the task id. Whoever may
            # watch it is whoever may enter its room, found through the task;
            # otherwise an outsider holding a task id finds no room and is let in.
            card = await TaskService(auth_session).get(self.topic_id)
            room_id = card.room_id if card is not None else self.topic_id
            project_id = await resolver.project_of_topic(room_id)
            if project_id is not None:
                try:
                    await resolver.authorize_topic(
                        actor, project_id=project_id, topic_id=room_id
                    )
                except ForbiddenError as exc:
                    return ("forbidden", exc.args[0]), [], card, None, None
            self.handle = actor.handle
            self.room_id = room_id if card is None else None
            # The turns still running here, as the database has them. The
            # broker's own list lives in this process only, and a deploy replaces
            # the process, so a turn started before it, or on the other
            # container while both ran, is missing from it though its agent is
            # still at work.
            place = await PlaceResolver(auth_session).conversation(self.topic_id)
            newest = (
                await newest_block_id(auth_session, place.conversation_id)
                if place is not None
                else None
            )
            return (
                None,
                await open_turns_on(auth_session, self.topic_id),
                card,
                newest,
                await self._snapshot(auth_session, chat_service, actor),
            )

    async def _snapshot(self, session, chat_service, actor) -> dict | None:
        """The room as it is now (`room_snapshot`), read after the subscription
        is registered. A room that cannot be read whole still opens: the client
        asks for each piece itself, as it would without a snapshot."""
        try:
            return await room_snapshot(session, chat_service, actor, self.topic_id)
        except Exception:
            _log.exception("chat_ws_snapshot_failed", topic=str(self.topic_id))
            await session.rollback()
            return None

    async def _relay(self, queue: SubscriberQueue, tagged) -> None:
        while True:
            frame = await queue.get()
            # The broker stopped feeding this room: it is publishing faster than
            # we can read and will not hold the backlog for us. Ending this one
            # room (the client resubscribes and refetches history) is how it
            # catches up; the other rooms on the connection are not behind.
            if isinstance(frame, SubscriberOverflow):
                _log.warning(
                    "chat_ws_subscriber_overflow",
                    topic=str(self.topic_id),
                    queued_bytes=queue.queued_bytes,
                )
                await tagged({"type": "closed", "code": 1013})
                return
            await tagged(frame)
