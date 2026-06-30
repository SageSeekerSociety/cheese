"""Chat WebSocket route.

The WS is a SUBSCRIBER, not the turn's owner (design §4 / v2 R1). A client
message is `submit`ted to the TurnRunner, which runs the turn as a background job
and publishes its frames to the Broker; this connection relays whatever frames
land on the topic channel. So a disconnect only drops the subscription — the turn
keeps running and persisting (invariant 2: the job doesn't depend on who watches),
and multiple connections to the same topic all see the live stream.

Protocol (unchanged frontend contract):
  client → {"type":"message","content": str, "author": str, "summon": bool}
  server → user_block / delta / tool / todo / state / event_block /
           assistant_block / error / done
"""

import asyncio
import contextlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.deps import get_broker, get_chat_service, get_turn_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import InProcessBroker, TurnRunner

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

    async def relay() -> None:
        # Subscribe BEFORE the first submit so no frame is missed (R10). replay=True
        # catches up the in-progress turn on a mid-turn (re)connect (R3); between
        # turns the buffer is empty, so a fresh connection replays nothing.
        async with broker.subscribe(channel, replay=True) as queue:
            while True:
                await send(await queue.get())

    relay_task = asyncio.create_task(relay())
    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "message":
                await send({"type": "error", "message": "unsupported message type"})
                continue
            content = (payload.get("content") or "").strip()
            author = payload.get("author") or "anonymous"
            # @芝士 toggle: summon the AI, or just post (spec C3, default post).
            summon = bool(payload.get("summon", False))
            if not content:
                await send({"type": "error", "message": "empty content"})
                continue
            # Fire-and-forget: the turn runs in the background and streams back
            # over the broker; this loop stays free to accept more messages.
            runner.submit(
                chat_service, topic_id, author=author, content=content, summon=summon
            )
    except WebSocketDisconnect:
        pass
    finally:
        relay_task.cancel()
        with contextlib.suppress(BaseException):
            await relay_task
