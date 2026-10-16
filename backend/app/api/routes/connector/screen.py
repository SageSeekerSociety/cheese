"""现场 viewer：一个浏览器看着某台设备屏幕的真实终端，还能往里打字。

``WS /connector/session/{sid}/screen`` 把浏览器终端与设备屏幕逐字节对接：TEXT 帧是
控制（JSON ``resize``），BINARY 帧是敲键 —— 输入走二进制道，控制消息就不可能被当成
打字，反过来也一样。只读是这件事要去的地方（见
``docs/where-a-turn-runs.md`` §6）。

门口那道闸在握手时问一次，之后每个 ``VIEW_RECHECK_EVERY_S`` 再问一次：看在不在，
落在登录态和成员身份上，而不是那枚 15 分钟就过期的 access token。

原来住在单文件 ``app/api/routes/connector.py`` 的「现场 viewer」那一段；门面在
``app/api/routes/connector/__init__.py``。
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.request_deps import DbSession
from app.auth.caller import handle_named_by
from app.common.auth import AccessClaims, verify_access_token
from app.domain.agent.device_hub import HubScreen, ViewerTransport, device_hub
from app.domain.device import owner_reads
from app.domain.user.sessions import SessionService

router = APIRouter(prefix="/connector", tags=["connector"])
logger = logging.getLogger(__name__)


class _WebSocketViewerTransport:
    """Adapts a live ``fastapi.WebSocket`` to the hub's ``ViewerTransport`` — it only
    ever *receives* raw screen bytes (the 现场 is relayed byte-for-byte, unparsed)."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_bytes(self, data: bytes) -> None:
        await self._websocket.send_bytes(data)


async def _sign_in_lasts(session: AsyncSession, claims: AccessClaims) -> bool:
    """Whether the sign-in the token names may still act at all.

    ``verify_access_token`` reads the signature and the expiry and nothing
    else, so a token outlives its session -- deliberately, for the length of a
    request. A connection is the caller that outlives it by far more: signed
    out, revoked from the device list, password changed or reset, idle past
    the timeout, or theft suspected, the session ends and the token keeps
    working until it expires. A token that names no session has none to ask
    about, and one that names no person has no session to look up.
    """
    if claims.sid is None or claims.user_id is None:
        return True
    sign_ins = SessionService(session)
    return await sign_ins.live_handle(claims.user_id, claims.sid) is not None


async def _may_view_screen(
    session: AsyncSession, screen: HubScreen, claims: AccessClaims | None
) -> bool:
    """Only a logged-in human who shares the screen's project (a project member/owner)
    or its topic (a topic-roster member) may watch its 现场 (P1 authz shape). Browsers
    can't set an Authorization header on a WS, so the token rides as ``?token=``.

    A screen working in a private channel, or in one of its tasks, is that
    channel's: only someone seated in it watches, project managers included —
    the same door as the channel's own routes (``authorize_topic``).

    Asked at the handshake and again on the viewer's clock, which is why the
    caller holds the claims rather than the token: what a lasting view stands
    on is the sign-in, not the 15-minute access token it was opened with.
    """
    if claims is None or not await _sign_in_lasts(session, claims):
        return False
    # Membership is keyed by handle, and the claims are read the same way the
    # workspace routes read them: an all-digit one is a NAME, not a user id.
    handle = await handle_named_by(session, claims)
    if screen.topic_id is not None:
        room = await owner_reads.room_of(session, screen.topic_id)
        if await owner_reads.members_only(session, room):
            return await owner_reads.topic_member(session, room, handle)
    if screen.project_id is not None:
        if await owner_reads.project_member(session, screen.project_id, handle):
            return True
        if await owner_reads.project_owner(session, screen.project_id) == handle:
            return True
    if screen.topic_id is not None:
        if await owner_reads.topic_member(session, screen.topic_id, handle):
            return True
    return False


def _as_int(value: object, default: int) -> int:
    """Best-effort terminal dimension from an untrusted browser frame — a malformed
    cols/rows must never crash the viewer socket."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return n if 0 < n <= 10000 else default


#: How often a live viewer is asked again whether it may still watch and type:
#: often enough that a sign-in which ended stops driving the machine, rarely
#: enough to cost nothing while a screen is only being watched.
VIEW_RECHECK_EVERY_S = 25.0


@router.websocket("/session/{sid}/screen")
async def viewer_socket(
    websocket: WebSocket,
    sid: str,
    db: DbSession,
    token: str | None = Query(default=None),
) -> None:
    """Relay one browser terminal ↔ one device screen, byte-for-byte.

    Frames split by type: TEXT is control (JSON ``resize``), BINARY is keystrokes
    forwarded to the pane. Keeping input on the binary channel means a control
    message can never be mistaken for typing, or the reverse.

    Input is deliberate, not incidental: whoever can type here can run anything on
    that machine as the agent's user. It is gated by the SAME check as watching —
    a logged-in member/owner of the screen's project, or a member of its topic —
    because that is already the trust boundary the agent itself runs inside, and a
    member who wants a shell there can otherwise just ask the agent for one.
    Nothing is forwarded before the viewer has attached, so keystrokes cannot
    reach a pane that was never subscribed.

    The door is asked again every ``VIEW_RECHECK_EVERY_S`` while the view lasts:
    the handshake is not the last word, so a view ends with the sign-in and the
    membership that opened it rather than at the next reconnect.

    Unknown-screen and not-authorized close identically (1008) so a screen id
    can't be enumerated."""
    screen = device_hub.screen(sid)
    claims = verify_access_token(token or "")
    authorized = (
        screen is not None
        and claims is not None
        and await _may_view_screen(db, screen, claims)
    )
    # Release the authz read-transaction before the viewer's (long-lived) relay loop.
    # A WS-injected ``get_db`` session lives until the socket closes, so leaving the
    # transaction open would park it `idle in transaction` for the whole view — the
    # same #356 footgun the device control channel hit (an idle-in-txn read lock
    # blocking device/topic-table migrations). Unknown-screen and not-authorized still
    # close identically (1008) so a screen id can't be enumerated.
    await db.commit()
    if screen is None or not authorized:
        await websocket.close(code=1008, reason="cannot view this screen")
        return

    await websocket.accept()
    transport: ViewerTransport = _WebSocketViewerTransport(websocket)
    device_id = screen.device_id
    attached = False  # attach (and subscribe) only once the viewer reports its size
    incoming: asyncio.Task[Any] | None = None  # a receive must not be cancelled
    # The review is a clock of its own, never a per-keystroke errand: the viewer
    # typing steadily is exactly the one whose sign-in must not lapse unnoticed,
    # so a busy view is asked as often as an idle one. The tick is made once a
    # period and is not remade when a frame arrives.
    review = asyncio.create_task(asyncio.sleep(VIEW_RECHECK_EVERY_S))
    try:
        while True:
            if incoming is None:
                incoming = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait(
                {incoming, review}, return_when=asyncio.FIRST_COMPLETED
            )
            if review in done:
                review = asyncio.create_task(asyncio.sleep(VIEW_RECHECK_EVERY_S))
                if not await _may_view_screen(db, screen, claims):
                    await websocket.close(code=4401, reason="this sign-in has ended")
                    break
                # The same footgun as the check before the handshake: a read left
                # open parks this socket's session `idle in transaction`.
                await db.commit()
                continue
            message = incoming.result()
            incoming = None
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None:
                data = message.get("bytes")
                # Before the first resize there is no subscription on the device,
                # so there is nothing to type into yet.
                if data and attached:
                    await device_hub.viewer_input(device_id, sid, data)
                continue
            try:
                ctrl = json.loads(text)
            except ValueError:
                continue
            if not isinstance(ctrl, dict) or ctrl.get("type") != "resize":
                continue
            cols = _as_int(ctrl.get("cols"), 120)
            rows = _as_int(ctrl.get("rows"), 32)
            if not attached:
                # First size for this viewer: attach + (if first viewer) subscribe at
                # the real size, matching the frozen web-claude contract.
                await device_hub.attach_viewer(
                    device_id, sid, transport, cols=cols, rows=rows
                )
                attached = True
            else:
                await device_hub.viewer_resize(device_id, sid, cols, rows)
    except WebSocketDisconnect:
        pass
    finally:
        review.cancel()
        if incoming is not None:
            incoming.cancel()
        if attached:
            await device_hub.detach_viewer(device_id, sid, transport)
