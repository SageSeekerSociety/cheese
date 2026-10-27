"""The machine's end of a preview's tunnel: one dial-out socket per seat.

A teammate's helper holds this websocket open, and both the browser's requests
and its HMR frames are carried over it. The browser's end — `relay_http`,
`relay_ws` — and `connection_hub`, which both ends ask for the running owner's
hub, are in `app.api.preview_relay`: the preview-host middleware needs them and
is not a route, so they may not live in a route module (C4
`routes-independent`).

Content hosts authorize viewers.
"""

import asyncio
import uuid

import anyio
from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.auth import PresentedCredential
from app.api.preview_relay import connection_hub
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import SEND_TIMEOUT_S

tunnel_router = APIRouter(prefix="/preview", tags=["preview"])


class _WebSocketPreviewTransport:
    """Adapts a live ``fastapi.WebSocket`` to the hub's ``PreviewTransport``."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_bytes(self, data: bytes) -> None:
        try:
            await self._websocket.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError) as exc:
            # The same translation the device link makes (connector.py): a peer
            # that dropped mid-write raises a disconnect, a socket Starlette
            # already closed a RuntimeError, and the hub handles neither — it
            # answers a lost transport, which is what both are. Left as they
            # were, a page asking for `/src/foo.ts` through a tunnel whose
            # helper had just gone was a 500 and an alert (2026-09-18), for a
            # preview that is simply not there.
            raise ConnectionError(str(exc)) from exc

    async def hang_up(self) -> None:
        try:
            await self._websocket.close(
                code=wire.CLOSE_SUPERSEDED, reason=_SUPERSEDED_REASON
            )
        except RuntimeError:
            pass


# What a helper that no longer carries its teammate's preview is told. It lands
# in that machine's cheese-preview.log, which is where anyone asking "why did my
# tunnel stop" will look.
_SUPERSEDED_REASON = "a newer launch of this teammate carries this room's preview"


@tunnel_router.websocket("/tunnel")
async def preview_tunnel(websocket: WebSocket, token: PresentedCredential) -> None:
    """The machine's dial-out preview tunnel, one per teammate in a room.

    Authenticated with the scoped cheese token the turn already runs on, so this
    transport widens nothing: the token names the topic, the topic names the
    audience, and a caller who cannot prove a topic is refused. It also names the
    teammate, which is the other half of the tunnel's key (see ``preview_hub``).
    Verification is an
    signature over the token's own claims, so this route touches no database — which
    also keeps it clear of the trap a long-lived socket falls into when it holds a
    request session open for the life of the connection (#356).
    """
    hub = connection_hub(websocket)
    claims = scoped_token_claims(token or "") or {}
    seat = claims.get("a")
    issued = claims.get("iat")
    try:
        # The conversation the helper serves: a room, or one of its tasks. A
        # room and its task can seat the same teammate, and each has its own
        # preview.
        topic_id = uuid.UUID(str(claims.get("t")))
    except (TypeError, ValueError):
        topic_id = None
    if topic_id is None or not isinstance(seat, str) or not seat:
        # Refused BEFORE accept: an unaccepted handshake is a plain HTTP 403 the
        # helper reports as a refusal (and stops retrying), instead of a socket
        # that opens and dies for reasons it cannot tell from a network fault.
        await websocket.close(
            code=1008,
            reason="a cheese token naming a topic and a teammate is required",
        )
        return
    if not hub.accepting:
        await websocket.accept()
        await websocket.close(code=1012, reason="preview owner draining")
        return
    offered = websocket.headers.get(wire.CAPS_HEADER, "").split(",")
    capabilities = wire.CAPABILITIES.intersection(part.strip() for part in offered)
    await websocket.accept(
        headers=[(wire.CAPS_HEADER.encode(), ",".join(sorted(capabilities)).encode())]
        if capabilities
        else None
    )
    transport = _WebSocketPreviewTransport(websocket)
    # A seat moves — a relaunched screen, a Cloud box replaced — and the helper
    # it left behind is still dialling. Hang up on the one being displaced: it
    # would otherwise sit here forever holding a socket nothing will ever speak
    # on again, and the close code tells it not to dial back in.
    displaced = hub.machine(topic_id, seat)
    machine = hub.attach(
        topic_id,
        seat,
        transport,
        issued=issued if isinstance(issued, int) else 0,
        capabilities=capabilities,
    )
    if machine is None:
        if hub.accepting:
            await transport.hang_up()
        else:
            await websocket.close(code=1012, reason="preview owner draining")
        return
    if displaced is not None and isinstance(
        displaced.transport, _WebSocketPreviewTransport
    ):
        await displaced.transport.hang_up()

    async def close_failed_tunnel() -> None:
        await machine.failed.wait()
        try:
            async with asyncio.timeout(SEND_TIMEOUT_S):
                await websocket.close(
                    code=1011 if hub.accepting else 1012,
                    reason="preview tunnel write failed"
                    if hub.accepting
                    else "preview owner draining",
                )
        except (TimeoutError, OSError, WebSocketDisconnect, RuntimeError):
            pass

    failed = asyncio.create_task(close_failed_tunnel())
    incoming = None
    try:
        while True:
            incoming = asyncio.create_task(websocket.receive())
            done, _ = await asyncio.wait(
                (incoming, failed), return_when=asyncio.FIRST_COMPLETED
            )
            if failed in done:
                break
            message = incoming.result()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                if len(data) > wire.MAX_WS_MESSAGE_BYTES + wire._HEAD.size:
                    await websocket.close(code=1009, reason="preview frame too large")
                    break
                machine.on_frame(data)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        failed.cancel()
        if incoming is not None:
            incoming.cancel()
        try:
            with anyio.CancelScope(shield=True):
                await asyncio.gather(
                    failed,
                    *([incoming] if incoming is not None else []),
                    return_exceptions=True,
                )
        finally:
            hub.detach(machine)
            await machine.drain()
