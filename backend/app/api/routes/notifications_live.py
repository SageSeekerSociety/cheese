"""Notices for the desktop app, the moment they happen.

Browser push never reaches the desktop app's web view, so the app holds a
connection of its own and shows what browser push would have said as system
notifications (`desktop/src-tauri/src/notices.rs`):

- ``POST /notifications/live-token``: the signed-in page asks for a credential
  that does nothing but open that connection, and hands it to the app. It is
  good for as long as the page's sign-in session is.
- ``WS /notifications/live`` with ``Authorization: Bearer <that credential>``:
  the app says where it stopped (``{"after": <id> | null}``), is sent what came
  since, then everything new as it is committed (`notification/live.py`), each
  time with the count of things waiting on the person. ``null`` means a first
  connection: nothing old is sent, only where "now" is. A beat every
  ``BEAT_SECONDS`` lets the app tell a dead connection from a quiet one, and is
  when a signed-out session closes the connection.

Frames sent: ``{"kind": "notices", "latest": <id> | null, "items": [...],
"away": <text>}``, ``{"kind": "waiting", "count": n}``, ``{"kind": "beat"}``.
Every text in them is in the person's language; ``away`` (present when there
are items) is the one line the app shows for a backlog too long to show one by
one.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.deps import get_chat_service
from app.api.response import ok
from app.api.routes.awaiting import waiting_items
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    create_notice_token,
    get_current_session_id,
    verify_notice_token,
)
from app.core.errors import BadRequestError
from app.domain.agent.chat import ChatService
from app.domain.notification.live import listening, notices_after
from app.domain.user.sessions import SessionService

router = APIRouter(prefix="", tags=["notifications"])

#: How long a connection may stay quiet before a beat is sent.
BEAT_SECONDS = 25.0

ChatDep = Annotated[ChatService, Depends(get_chat_service)]


@router.post("/notifications/live-token")
async def live_token(
    user: Annotated[AuthUserInfo, Depends(require_auth_user)],
    sid: Annotated[uuid.UUID | None, Depends(get_current_session_id)],
) -> dict:
    if sid is None:
        raise BadRequestError("This sign-in has no session to tie notices to")
    return ok({"token": create_notice_token(user.user_id, sid)})


async def _notices_after(chat: ChatService, user_id: int, after: int | None) -> dict:
    async with chat.session_factory() as db:
        return {"kind": "notices", **await notices_after(db, user_id, after)}


async def _waiting(chat: ChatService, user_id: int, handle: str) -> dict:
    async with chat.session_factory() as db:
        items = await waiting_items(db, chat, handle=handle, user_id=user_id)
    return {"kind": "waiting", "count": len(items)}


async def _signed_in(chat: ChatService, user_id: int, sid: uuid.UUID) -> str | None:
    """The handle whose sign-in this is, while it lasts."""
    async with chat.session_factory() as db:
        return await SessionService(db).live_handle(user_id, sid)


@router.websocket("/notifications/live")
async def live(websocket: WebSocket, chat: ChatDep) -> None:
    bearer = websocket.headers.get("authorization", "")
    claims = verify_notice_token(bearer.removeprefix("Bearer ").strip())
    handle = await _signed_in(chat, *claims) if claims is not None else None
    if claims is None or handle is None:
        # Refused before the handshake completes: the app sees a 403 and waits
        # for the page to sign in again instead of retrying.
        await websocket.close()
        return
    user_id, sid = claims

    await websocket.accept()
    with listening(user_id) as woken, contextlib.suppress(WebSocketDisconnect):
        opening = await websocket.receive_json()
        after = opening.get("after") if isinstance(opening, dict) else None
        frame = await _notices_after(
            chat, user_id, after if isinstance(after, int) else None
        )
        cursor = frame["latest"] or 0
        await websocket.send_json(frame)
        await websocket.send_json(await _waiting(chat, user_id, handle))

        # The app sends nothing more; reading is only how a close is noticed.
        hung_up = asyncio.create_task(_until_closed(websocket))
        try:
            while True:
                wake = asyncio.create_task(woken.wait())
                done, _ = await asyncio.wait(
                    {wake, hung_up},
                    timeout=BEAT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if hung_up in done:
                    wake.cancel()
                    return
                if wake in done:
                    woken.clear()
                    frame = await _notices_after(chat, user_id, cursor)
                    cursor = frame["latest"] or cursor
                    if frame["items"]:
                        await websocket.send_json(frame)
                    await websocket.send_json(await _waiting(chat, user_id, handle))
                    continue
                wake.cancel()
                if await _signed_in(chat, user_id, sid) is None:
                    await websocket.close(code=4401)
                    return
                await websocket.send_json({"kind": "beat"})
        finally:
            hung_up.cancel()


async def _until_closed(websocket: WebSocket) -> None:
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while True:
            await websocket.receive_text()
