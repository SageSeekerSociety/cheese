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
  when a signed-out session closes the connection. Two backend slots run side by
  side during a deploy, and a notice committed in the other one wakes nobody
  here (`notification/live.py`), so a beat also compares the cursor with the
  newest notice in the database and catches up: at worst ``BEAT_SECONDS`` behind.

Frames sent: ``{"kind": "notices", "latest": <id> | null, "items": [...],
"away": <text>}``, ``{"kind": "waiting", "count": n}``, ``{"kind": "beat"}``.
Every text in them is in the person's language; ``away`` (present when there
are items) is the one line the app shows for a backlog too long to show one by
one.

A browser showing the inbox feed holds a lighter connection of its own:

- ``WS /notifications/inbox``: a browser cannot set a header on a WebSocket, so
  the same credential comes as the first message (``{"token": ...}``) rather
  than in the URL, where access logs would keep it. Unanswered for
  ``SIGN_IN_SECONDS``, or with a credential that does not hold, the connection
  is closed with 4401, which tells the page to ask for a new one.
- It is sent ``{"kind": "inbox", "latest": <id> | null}``, the newest row in
  the person's inbox of any type, on opening and whenever that changes; the page
  reads the rows itself. ``{"kind": "assistant", "conversation": <id>}`` says
  one of the person's conversations with 芝士 said more, so a panel open on it
  reads it again. Nothing else is computed per wake — not the waiting
  count the app's badge needs — because a browser has one open on every feed. A
  beat works as it does for the app.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.awaiting_deps import all_waiting
from app.api.deps import get_chat_service
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    create_notice_token,
    get_current_session_id,
    verify_notice_token,
)
from app.core import credentials
from app.core.errors import BadRequestError
from app.domain.agent.chat import ChatService
from app.domain.agent.personal.service import AssistantConversations
from app.domain.notification.live import (
    behind,
    latest_in_inbox,
    listening,
    notices_after,
)
from app.domain.user.sessions import SessionService

router = APIRouter(prefix="", tags=["notifications"])

#: How long a connection may stay quiet before a beat is sent.
BEAT_SECONDS = 25.0
#: How long a browser's connection may stay open before saying who it is.
SIGN_IN_SECONDS = 10.0
#: Close code: the credential does not hold (any more); ask for a new one.
SIGNED_OUT = 4401

ChatDep = Annotated[ChatService, Depends(get_chat_service)]


@router.post("/notifications/live-token", dependencies=[ROUTE_DECIDES])
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
        items = await all_waiting(db, handle=handle, user_id=user_id)
    return {"kind": "waiting", "count": len(items)}


async def _signed_in(chat: ChatService, user_id: int, sid: uuid.UUID) -> str | None:
    """The handle whose sign-in this is, while it lasts."""
    async with chat.session_factory() as db:
        return await SessionService(db).live_handle(user_id, sid)


async def _behind(chat: ChatService, user_id: int, cursor: int) -> bool:
    """Whether a notice this connection has not been sent is already committed.

    One max(id) per beat, on its own session.
    """
    async with chat.session_factory() as db:
        return await behind(db, user_id, cursor)


@router.websocket("/notifications/live")
async def live(websocket: WebSocket, chat: ChatDep) -> None:
    claims = verify_notice_token(credentials.presented(websocket.headers))
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
        # The beat is a clock of its own, not the wait's timeout. As a timeout
        # it was restarted by every notice that arrived, so the connection that
        # heard the most — an active room, a running task, a second device —
        # was the one never checked, and a session that had ended kept its
        # connection for as long as traffic lasted. The tick is made once a
        # period and is not remade when a wake comes in, so a busy connection
        # is checked as often as a quiet one.
        beat = asyncio.create_task(asyncio.sleep(BEAT_SECONDS))
        try:
            while True:
                wake = asyncio.create_task(woken.wait())
                done, _ = await asyncio.wait(
                    {wake, hung_up, beat}, return_when=asyncio.FIRST_COMPLETED
                )
                if hung_up in done:
                    wake.cancel()
                    return
                if beat in done:
                    beat = asyncio.create_task(asyncio.sleep(BEAT_SECONDS))
                    # A wake that arrives in the same round is not lost: the
                    # catch-up below reads by cursor, not by wake.
                    wake.cancel()
                    if await _signed_in(chat, user_id, sid) is None:
                        await websocket.close(code=SIGNED_OUT)
                        return
                    # The beat's other errand. A notice committed by the other
                    # backend slot during a deploy wakes nobody in this process:
                    # read where "now" is, and catch up rather than stay quiet
                    # until the app gives up and reconnects.
                    if not await _behind(chat, user_id, cursor):
                        await websocket.send_json({"kind": "beat"})
                        continue
                elif wake in done:
                    woken.clear()

                frame = await _notices_after(chat, user_id, cursor)
                cursor = frame["latest"] or cursor
                if frame["items"]:
                    await websocket.send_json(frame)
                await websocket.send_json(await _waiting(chat, user_id, handle))
        finally:
            hung_up.cancel()
            beat.cancel()


async def _latest(chat: ChatService, user_id: int) -> int | None:
    async with chat.session_factory() as db:
        return await latest_in_inbox(db, user_id)


async def _said_to(chat: ChatService, user_id: int) -> tuple | None:
    """The newest message in the person's conversations with 芝士, and its
    conversation."""
    async with chat.session_factory() as db:
        return await AssistantConversations(db).newest(user_id)


async def _sign_in(
    websocket: WebSocket, chat: ChatService
) -> tuple[int, uuid.UUID] | None:
    """Who the first message says this is, if that sign-in still holds."""
    with contextlib.suppress(TimeoutError, ValueError, KeyError, TypeError):
        opening = await asyncio.wait_for(websocket.receive_json(), SIGN_IN_SECONDS)
        claims = verify_notice_token(str(opening["token"]))
        if claims is not None and await _signed_in(chat, *claims) is not None:
            return claims
    return None


@router.websocket("/notifications/inbox")
async def inbox(websocket: WebSocket, chat: ChatDep) -> None:
    await websocket.accept()
    with contextlib.suppress(WebSocketDisconnect):
        claims = await _sign_in(websocket, chat)
        if claims is None:
            await websocket.close(code=SIGNED_OUT)
            return
        user_id, sid = claims
        with listening(user_id, inbox=True) as woken:
            latest = await _latest(chat, user_id)
            said = await _said_to(chat, user_id)
            await websocket.send_json({"kind": "inbox", "latest": latest})
            hung_up = asyncio.create_task(_until_closed(websocket))
            # A clock of its own, as in `live` above, so a busy connection is
            # checked as often as a quiet one.
            beat = asyncio.create_task(asyncio.sleep(BEAT_SECONDS))
            try:
                while True:
                    wake = asyncio.create_task(woken.wait())
                    done, _ = await asyncio.wait(
                        {wake, hung_up, beat}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if hung_up in done:
                        wake.cancel()
                        return
                    beat_round = beat in done
                    if beat_round:
                        beat = asyncio.create_task(asyncio.sleep(BEAT_SECONDS))
                        wake.cancel()
                        if await _signed_in(chat, user_id, sid) is None:
                            await websocket.close(code=SIGNED_OUT)
                            return
                    else:
                        woken.clear()
                    # On a beat this is also the catch-up for a row the other
                    # backend slot committed during a deploy, which woke nobody here.
                    now = await _latest(chat, user_id)
                    told = now != latest
                    if told:
                        latest = now
                        await websocket.send_json({"kind": "inbox", "latest": latest})
                    # A conversation with 芝士 said more (an answer ended, a
                    # routine ran): a panel open on it reads it again.
                    newer = await _said_to(chat, user_id)
                    if newer is not None and newer != said:
                        said = newer
                        told = True
                        await websocket.send_json(
                            {"kind": "assistant", "conversation": str(newer[1])}
                        )
                    if not told and beat_round:
                        await websocket.send_json({"kind": "beat"})
            finally:
                hung_up.cancel()
                beat.cancel()


async def _until_closed(websocket: WebSocket) -> None:
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while True:
            await websocket.receive_text()
