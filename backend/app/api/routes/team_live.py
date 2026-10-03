"""A team's live feed: tells its open pages which of its resources changed.

Protocol (the room socket's, `chat.py`, minus everything about turns):
  connect → /api/teams/{id}/live?token=<session token>   (required)
  client → {"type":"ping"}  →  server → {"type":"pong"}
  server → {"type":"state","resource":"machines","project_ids":[...]}: read
           those projects' machines again.

A frame names what changed and carries no state, so a page that was
disconnected reads everything again when it reconnects. The connect check
refuses with the room socket's three codes (`auth_required`, `auth_expired`,
`forbidden`) and closes 1008, so the client's refusal latch is the same one.
"""

import asyncio
import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.api.auth import ActorResolver
from app.api.deps import get_broker, get_chat_service
from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.core.sentences import error_frame, say
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import InProcessBroker
from app.domain.machine.live import team_channel

router = APIRouter(tags=["teams"])


@router.websocket("/teams/{team_id}/live")
async def team_live(
    websocket: WebSocket,
    team_id: int,
    chat_service: Annotated[ChatService, Depends(get_chat_service)],
    broker: Annotated[InProcessBroker, Depends(get_broker)],
) -> None:
    send_lock = asyncio.Lock()

    async def send(frame: dict) -> None:
        async with send_lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await websocket.send_json(frame)

    async def relay(queue: asyncio.Queue[dict]) -> None:
        while True:
            await send(await queue.get())

    token = websocket.query_params.get("token") or ""
    refusal: tuple[str, str] | None = None
    # Subscribed before accepting, as the room socket is: "open" then already
    # means this socket hears every change from here on.
    async with broker.subscribe(team_channel(team_id)) as queue:
        async with chat_service.session_factory() as auth_session:
            resolver = ActorResolver(
                session=auth_session, bearer=token or None, cheese_token=""
            )
            actor = await resolver.resolve()
            if not actor.authenticated:
                refusal = (
                    ("auth_expired", say("signInAgain"))
                    if token
                    else ("auth_required", say("signInFirst"))
                )
            else:
                try:
                    await resolver.authorize_team(actor, team_id=team_id)
                except AuthenticationRequiredError as exc:
                    refusal = ("auth_expired", exc.args[0])
                except ForbiddenError as exc:
                    refusal = ("forbidden", exc.args[0])
        await websocket.accept()
        if refusal is not None:
            code, message = refusal
            await send(error_frame(message, type="error", code=code))
            await websocket.close(code=1008)
            return

        relay_task = asyncio.create_task(relay(queue))
        try:
            while websocket.application_state == WebSocketState.CONNECTED:
                payload = await websocket.receive_json()
                if payload.get("type") == "ping":
                    await send({"type": "pong"})
                    continue
                await send({"type": "error", "message": "unsupported message type"})
        except WebSocketDisconnect:
            pass
        finally:
            relay_task.cancel()
            with contextlib.suppress(BaseException):
                await relay_task
