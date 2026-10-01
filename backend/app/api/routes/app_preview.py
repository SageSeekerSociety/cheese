"""Carry HTTP and HMR over the machine dial-out tunnel.

Content hosts authorize viewers.
"""

import asyncio
import re
import uuid
from http.cookies import CookieError, SimpleCookie

import anyio
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from app.api import proxy
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import SEND_TIMEOUT_S, PreviewStream, preview_hub

tunnel_router = APIRouter(prefix="/preview", tags=["preview"])

# Handshake headers that belong to THIS leg and must not be relayed to the next
# one. The machine performs its own handshake to the app, so a forwarded key or
# version arrives twice; and ``extensions`` is worse than redundant — accepting
# permessage-deflate there would have the app compress frames the relay does not
# decompress, which corrupts the stream rather than failing it. The subprotocol
# is deliberately NOT here: negotiating it end to end is the whole point.
_WS_HANDSHAKE_OWNED = {
    "sec-websocket-key",
    "sec-websocket-version",
    "sec-websocket-extensions",
    "sec-websocket-accept",
}
_PREVIEW_COOKIES = {"__Host-cheese-preview", "cheese-preview-local"}


def _upstream_path(conn: Request | WebSocket) -> str:
    """The app owns the entire preview origin, including its query parameters."""
    return conn.url.path + ("?" + conn.url.query if conn.url.query else "")


def _app_headers(conn: Request | WebSocket) -> list[tuple[str, str]]:
    # Platform login credentials never enter the content origin. Authorization
    # here belongs to the application; only Cheese's reserved proof is removed.
    headers = [
        (key, value)
        for key, value in conn.headers.items()
        if key.lower() not in proxy.DROP_HEADERS | {"host", "cookie", "accept-encoding"}
        and not key.lower().startswith("x-cheese-")
    ]
    cookies = [
        part.strip()
        for part in conn.headers.get("cookie", "").split(";")
        if "=" in part and part.split("=", 1)[0].strip() not in _PREVIEW_COOKIES
    ]
    if cookies:
        headers.append(("cookie", "; ".join(cookies)))
    return headers


def _app_cookies(value: str) -> list[str]:
    # Supported Python versions do not recognize CHIPS' valueless attribute.
    value = re.sub(r";\s*Partitioned\s*(?=;|$)", "", value, flags=re.I)
    parsed: SimpleCookie = SimpleCookie()
    try:
        parsed.load(value)
    except CookieError:
        return []
    result = []
    for name, cookie in parsed.items():
        if name in _PREVIEW_COOKIES:
            continue
        # Ordinary Lax sessions cannot log in inside the cross-site panel.
        # Partition the app session by top-level site and keep it host-only.
        cookie["domain"] = ""
        cookie["secure"] = True
        cookie["samesite"] = "None"
        result.append(cookie.OutputString() + "; Partitioned")
    return result


# --- the machine's end ---------------------------------------------------------


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
async def preview_tunnel(
    websocket: WebSocket, token: str | None = Query(default=None)
) -> None:
    """The machine's dial-out preview tunnel, one per teammate in a room.

    Authenticated with the scoped cheese token the turn already runs on, so this
    transport widens nothing: the token names the topic, the topic names the
    audience, and a caller who cannot prove a topic is refused. It also names the
    teammate, which is the other half of the tunnel's key (see ``preview_hub``).
    Verification is an
    HMAC over the token's own claims, so this route touches no database — which
    also keeps it clear of the trap a long-lived socket falls into when it holds a
    request session open for the life of the connection (#356).
    """
    claims = scoped_token_claims(token or "") or {}
    seat = claims.get("a")
    issued = claims.get("iat")
    try:
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
    displaced = preview_hub.machine(topic_id, seat)
    machine = preview_hub.attach(
        topic_id,
        seat,
        transport,
        issued=issued if isinstance(issued, int) else 0,
        capabilities=capabilities,
    )
    if machine is None:
        await transport.hang_up()
        return
    if displaced is not None and isinstance(
        displaced.transport, _WebSocketPreviewTransport
    ):
        await displaced.transport.hang_up()

    async def close_failed_tunnel() -> None:
        await machine.failed.wait()
        try:
            async with asyncio.timeout(SEND_TIMEOUT_S):
                await websocket.close(code=1011, reason="preview tunnel write failed")
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
            preview_hub.detach(machine)
            await machine.drain()


# --- the browser's end ---------------------------------------------------------


class _PreviewStreamingResponse(StreamingResponse):
    def __init__(self, upstream):
        self.upstream = upstream
        super().__init__(upstream.iter_bytes(), status_code=upstream.status)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.upstream.aclose()


def _response_headers(headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    dropped = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-frame-options",
    }
    for name, value in headers:
        if name.lower() == "connection":
            dropped.update(part.strip().lower() for part in value.split(","))
    return [(k, v) for k, v in headers if k.lower() not in dropped]


async def relay_http(topic_id: uuid.UUID, seat: str, request: Request) -> Response:
    """Forward an already authorized content-host request without URL rewriting,
    to the app ``seat`` (the teammate who declared it) is serving."""
    body = await request.body()

    async def disconnected():
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    waiting = asyncio.create_task(
        preview_hub.request_stream(
            topic_id,
            seat,
            method=request.method,
            path=_upstream_path(request),
            headers=_app_headers(request),
            body=body,
        )
    )
    gone = asyncio.create_task(disconnected())
    upstream = None
    transferred = False
    try:
        try:
            done, _ = await asyncio.wait(
                {waiting, gone}, return_when=asyncio.FIRST_COMPLETED
            )
            if gone in done:
                return Response(status_code=404, content=b"preview unavailable")
            upstream = await waiting
        finally:
            gone.cancel()
            if not waiting.done():
                waiting.cancel()
            await asyncio.gather(waiting, gone, return_exceptions=True)
        if upstream is None:
            return Response(status_code=404, content=b"preview unavailable")
        kept = _response_headers(upstream.headers)
        media_type = next((v for k, v in kept if k.lower() == "content-type"), None)
        response = _PreviewStreamingResponse(upstream)
        # Keep app sessions without allowing an app to replace preview auth.
        for name, value in kept:
            if name.lower() == "set-cookie":
                for cookie in _app_cookies(value):
                    response.headers.append("set-cookie", cookie)
            elif name.lower() != "content-type":
                response.headers.append(name, value)
        if media_type:
            response.headers["content-type"] = media_type
        # No await between ownership transfer and returning the response owner.
        transferred = True
        return response
    finally:
        if not transferred:
            if upstream is None and waiting.done() and not waiting.cancelled():
                error = waiting.exception()
                if error is None:
                    upstream = waiting.result()
            if upstream is not None:
                await upstream.aclose()


async def relay_ws(websocket: WebSocket, topic_id: uuid.UUID, seat: str) -> None:
    """Pump the authorized preview's HMR socket without holding a DB session."""
    stream = preview_hub.open_stream(topic_id, seat)
    if stream is None:
        await websocket.close(code=1011)
        return
    try:
        await stream.send(
            wire.OP_WS_OPEN,
            wire.encode_meta(
                {
                    "path": _upstream_path(websocket),
                    "headers": [
                        [k, v]
                        for k, v in _app_headers(websocket)
                        if k.lower() not in _WS_HANDSHAKE_OWNED
                    ],
                }
            ),
        )
        op, payload = await stream.receive()
        if op != wire.OP_WS_OK:
            await websocket.close(code=1011)
            return
        meta, _ = wire.decode_meta(payload)
        subprotocol = str(meta.get("subprotocol") or "") or None
        await websocket.accept(subprotocol=subprotocol)
        await _pump(websocket, stream)
    except (TimeoutError, ValueError, OSError, RuntimeError, WebSocketDisconnect):
        pass
    finally:
        await stream.aclose()
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close(code=1011, reason="preview connection ended")
            except (OSError, RuntimeError, WebSocketDisconnect):
                pass


async def _pump(browser: WebSocket, stream: PreviewStream) -> None:
    """Run both directions until either side closes, then tear the other down."""

    async def browser_to_app() -> None:
        try:
            while True:
                message = await browser.receive()
                if message["type"] == "websocket.disconnect":
                    payload = b""
                    if stream.close_metadata:
                        payload = wire.close_payload(
                            *wire.parse_close(
                                wire.close_payload(
                                    message.get("code", 1001), message.get("reason", "")
                                )
                            )
                        )
                    await stream.aclose(payload=payload)
                    return
                data = message.get("bytes")
                if data is not None:
                    await stream.send(wire.OP_WS_MSG, bytes([wire.WS_BINARY]) + data)
                    continue
                text = message.get("text")
                if text is not None:
                    await stream.send(
                        wire.OP_WS_MSG, bytes([wire.WS_TEXT]) + text.encode()
                    )
        except (WebSocketDisconnect, OSError, RuntimeError):
            return

    async def app_to_browser() -> None:
        while True:
            try:
                # No timeout: an HMR socket is silent for as long as nobody edits
                # a file, and cutting it on silence would make the page reconnect
                # forever — the symptom this proxy exists to avoid.
                op, payload = await stream.receive(timeout=None)
            except (TimeoutError, RuntimeError):
                return
            if op == wire.OP_CLOSE:
                if stream.close_metadata:
                    code, reason = wire.parse_close(payload)
                else:
                    code, reason = 1011, "preview upstream disconnected"
                await browser.close(code=code, reason=reason)
                return
            if op != wire.OP_WS_MSG or not payload:
                await browser.close(code=1011, reason="preview upstream failed")
                return
            if payload[0] == wire.WS_TEXT:
                await browser.send_text(payload[1:].decode(errors="replace"))
            else:
                await browser.send_bytes(payload[1:])

    up = asyncio.create_task(browser_to_app())
    down = asyncio.create_task(app_to_browser())
    try:
        await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Session expiry also cancels this pump; reap both directions before
        # releasing its stream so a dormant HMR connection cannot leak tasks.
        for task in (up, down):
            task.cancel()
        await asyncio.gather(up, down, return_exceptions=True)
        await stream.aclose()
