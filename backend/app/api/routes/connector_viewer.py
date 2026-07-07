"""The 现场 viewer WebSocket route (Act 2 step 4, relay mechanics).

Relays one browser terminal ↔ one device screen, byte-for-byte per the frozen
web-claude contract: the first viewer's first ``resize`` drives a ``screen.subscribe``
carrying its real size (so the device attaches a tmux sized to it); later resizes
are ``screen.resize``; keystrokes are ``screen.input`` (base64); the viewer's mode
(passive/scroll/full) becomes the ``viewerLevel`` variable; a device ``screen.data``
frame is fanned out to every viewer.

**Authorization is injected, not decided here.** ``authorize_viewer(screen, ws)``
is the seam where the real policy lives — only a logged-in *project member* may watch
a project's screen (architecture §5). How the browser authenticates (cookie / token)
and how membership is checked are the interactive-session design choices; this route
just calls the seam and closes 1008 on refusal. The relay mechanics below are the
frozen part and are the same in every deployment.
"""

import json
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent.hub import DeviceHub, HubScreen
from app.domain.device.service import DeviceService

ViewerAuthorizer = Callable[[HubScreen, WebSocket], Awaitable[bool]]


class _WebSocketViewerTransport:
    """Adapts a live ``fastapi.WebSocket`` to the hub's ``ViewerTransport`` (it only
    ever receives raw screen bytes)."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_bytes(self, data: bytes) -> None:
        await self._websocket.send_bytes(data)


async def _deny(screen: HubScreen, websocket: WebSocket) -> bool:
    return False


def build_viewer_router(
    device_service: DeviceService,
    hub: DeviceHub,
    authorize_viewer: ViewerAuthorizer = _deny,
) -> APIRouter:
    router = APIRouter(prefix="/connector", tags=["connector"])

    @router.websocket("/session/{sid}/screen")
    async def viewer_socket(websocket: WebSocket, sid: str) -> None:
        # Unknown-screen and not-authorized close identically, so an unauthenticated
        # caller cannot distinguish "this screen id exists" from "exists but you may
        # not view it" — no screen-id enumeration signal.
        screen = hub.screen(sid)
        if screen is None or not await authorize_viewer(screen, websocket):
            await websocket.close(code=1008, reason="cannot view this screen")
            return

        await websocket.accept()
        transport = _WebSocketViewerTransport(websocket)
        device_id = screen.device_id
        attached = False  # we attach (and subscribe) only once we know the viewer's size
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data_bytes = message.get("bytes")
                data_text = message.get("text")
                if data_bytes is not None:
                    if attached:  # ignore keystrokes until the terminal has a size
                        await hub.viewer_input(device_id, sid, data_bytes)
                elif data_text is not None:
                    attached = await _handle_viewer_control(
                        hub, device_id, sid, transport, data_text, attached
                    )
        except WebSocketDisconnect:
            pass
        finally:
            if attached:
                await hub.detach_viewer(device_id, sid, transport)

    return router


async def _handle_viewer_control(
    hub: DeviceHub,
    device_id: str,
    sid: str,
    transport: _WebSocketViewerTransport,
    text: str,
    attached: bool,
) -> bool:
    """Process one control frame; return the (possibly updated) ``attached`` flag."""
    try:
        ctrl = json.loads(text)
    except ValueError:
        return attached
    if not isinstance(ctrl, dict):
        return attached
    kind = ctrl.get("type")
    if kind == "resize":
        cols, rows = _as_int(ctrl.get("cols"), 120), _as_int(ctrl.get("rows"), 32)
        if not attached:
            # First size for this viewer: attach + (if first viewer) subscribe at
            # the real size, matching the frozen contract.
            await hub.attach_viewer(device_id, sid, transport, cols=cols, rows=rows)
            return True
        await hub.viewer_resize(device_id, sid, cols, rows)
    elif kind == "control":
        await hub.viewer_control(device_id, sid, str(ctrl.get("level", "passive")))
    return attached


def _as_int(value: object, default: int) -> int:
    """Best-effort terminal dimension from an untrusted browser frame — a malformed
    cols/rows must never crash the viewer socket."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return n if 0 < n <= 10000 else default
