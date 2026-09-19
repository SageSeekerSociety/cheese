"""Carry HTTP and HMR over the machine dial-out tunnel.

Content hosts authorize viewers.
"""

import asyncio
import re
import uuid
from http.cookies import CookieError, SimpleCookie

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api import proxy
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewStream, preview_hub

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
            await self._websocket.close(code=1000)
        except RuntimeError:
            pass


@tunnel_router.websocket("/tunnel")
async def preview_tunnel(
    websocket: WebSocket, token: str | None = Query(default=None)
) -> None:
    """The machine's dial-out preview tunnel, one per topic.

    Authenticated with the scoped cheese token the turn already runs on, so this
    transport widens nothing: the token names the topic, the topic names the
    audience, and a caller who cannot prove a topic is refused. Verification is an
    HMAC over the token's own claims, so this route touches no database — which
    also keeps it clear of the trap a long-lived socket falls into when it holds a
    request session open for the life of the connection (#356).
    """
    claims = scoped_token_claims(token or "")
    raw_topic = (claims or {}).get("t")
    try:
        topic_id = uuid.UUID(str(raw_topic))
    except (TypeError, ValueError):
        # Refused BEFORE accept: an unaccepted handshake is a plain HTTP 403 the
        # helper reports as a refusal (and stops retrying), instead of a socket
        # that opens and dies for reasons it cannot tell from a network fault.
        await websocket.close(
            code=1008, reason="a valid topic-scoped cheese token is required"
        )
        return
    await websocket.accept()
    transport = _WebSocketPreviewTransport(websocket)
    # A topic runs on one machine at a time, and it moves — a relaunched screen,
    # a Cloud box replaced. Hang up on the one being displaced: it would otherwise
    # sit here forever holding a socket nothing will ever speak on again, and its
    # own end has no way to notice it has been superseded.
    displaced = preview_hub.machine(topic_id)
    preview_hub.attach(topic_id, transport)
    if displaced is not None and isinstance(
        displaced.transport, _WebSocketPreviewTransport
    ):
        await displaced.transport.hang_up()
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                preview_hub.on_frame(topic_id, data)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        preview_hub.detach(topic_id, transport)


# --- the browser's end ---------------------------------------------------------


async def relay_http(topic_id: uuid.UUID, request: Request) -> Response:
    """Forward an already authorized content-host request without URL rewriting."""
    upstream = await preview_hub.request(
        topic_id,
        method=request.method,
        path=_upstream_path(request),
        headers=_app_headers(request),
        body=await request.body(),
    )
    if upstream is None:
        return Response(status_code=404, content=b"preview unavailable")
    kept = [
        (k, v)
        for k, v in upstream.headers
        if k.lower() not in proxy.DROP_HEADERS | {"x-frame-options"}
    ]
    media_type = next((v for k, v in kept if k.lower() == "content-type"), None)
    body = upstream.body
    response = Response(content=body, status_code=upstream.status)
    # Keep application sessions without allowing an app to replace preview auth.
    for name, value in kept:
        if name.lower() == "set-cookie":
            for cookie in _app_cookies(value):
                response.headers.append("set-cookie", cookie)
        elif name.lower() != "content-type":
            response.headers.append(name, value)
    if media_type:
        response.headers["content-type"] = media_type
    return response


async def relay_ws(websocket: WebSocket, topic_id: uuid.UUID) -> None:
    """Pump the authorized preview's HMR socket without holding a DB session."""
    stream = preview_hub.open_stream(topic_id)
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
        stream.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass


async def _pump(browser: WebSocket, stream: PreviewStream) -> None:
    """Run both directions until either side closes, then tear the other down."""

    async def browser_to_app() -> None:
        try:
            while True:
                message = await browser.receive()
                if message["type"] == "websocket.disconnect":
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
            if op != wire.OP_WS_MSG or not payload:
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
        await stream.send(wire.OP_CLOSE)
