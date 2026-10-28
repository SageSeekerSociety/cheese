"""The browser's end of a preview's tunnel, and the hub both ends speak to.

A preview's traffic has two ends. The machine's end is the dial-out websocket its
helper holds open (`routes/app_preview.py`); the browser's end is here — an
already authorised content-host request is forwarded, unchanged, to the teammate
serving that app, and its HMR socket is pumped both ways. `connection_hub` is
what both ends ask for the hub: the running owner's, or the in-process one.

This is the half `app.api.preview_host` (the middleware that intercepts preview
hosts) and the machine's route share, and neither may import the other: left
inside `routes/app_preview.py`, `preview_host` reached a route and C4
(`routes-independent`) reported the chain `preview_sessions -> preview_host ->
app_preview`. Nothing here is a route, so it belongs under `app.api` with the
rest of what the two ends share.
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from http.cookies import CookieError, SimpleCookie

import anyio
from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from app.api import proxy
from app.api.preview_interstitial import (
    UNAVAILABLE,
    is_document_navigation,
    preview_unavailable_response,
)
from app.api.preview_runtime import HTML_MEDIA, inject_runtime_script
from app.core.config import settings
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import (
    PreviewAdmissionError,
    PreviewHub,
    PreviewStream,
    preview_hub,
)


def connection_hub(conn: Request | WebSocket) -> PreviewHub:
    state = getattr(conn.scope.get("app"), "state", None)
    hub = getattr(state, "preview_hub", None)
    if hub is None and settings.preview_connection_mode == "owner":
        raise PreviewAdmissionError("transport_unavailable")
    return preview_hub if hub is None else hub


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


# --- the browser's end ---------------------------------------------------------


class _PreviewStreamingResponse(StreamingResponse):
    def __init__(self, upstream, body: AsyncIterator[bytes] | None = None):
        self.upstream = upstream
        super().__init__(
            upstream.iter_bytes() if body is None else body, status_code=upstream.status
        )

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


# 应用的网页和房间文件一样装上运行时桥：右栏的小浏览器靠它读出页面此刻的地址、替读者
# 后退前进、在应用睡着之前留一份静态快照。脚本只插在文档开头那一段里（见
# `inject_runtime_script`），所以只缓住前 64 KiB 找插入点，其余照旧边收边发——一份边渲
# 染边吐的页面不会因此等到整页收完。
_INJECT_PREFIX_BYTES = 64 * 1024


def _injects(method: str, status: int, headers: list[tuple[str, str]]) -> bool:
    """这一个应答是不是一份要装运行时的网页文档。

    只认 GET 拿到的 200 HTML；被压缩过的不动——按字节插进一段 gzip 只会把它弄坏。
    平台转发时已经拿掉了 accept-encoding，应用照理不会压缩，这一条是防它自作主张。
    """
    if method != "GET" or status != 200:
        return False
    fields = {k.lower(): v for k, v in headers}
    media = fields.get("content-type", "").split(";", 1)[0].strip().lower()
    encoding = fields.get("content-encoding", "identity").strip().lower()
    return media in HTML_MEDIA and encoding in {"", "identity"}


async def _with_runtime(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    head = bytearray()
    async for chunk in chunks:
        head.extend(chunk)
        if len(head) >= _INJECT_PREFIX_BYTES:
            yield inject_runtime_script(bytes(head))
            async for rest in chunks:
                yield rest
            return
    yield inject_runtime_script(bytes(head))


async def relay_http(
    topic_id: uuid.UUID, seat: str, request: Request, *, instance: str | None = None
) -> Response:
    """Forward an already authorized content-host request without URL rewriting,
    to the app ``seat`` (the teammate who declared it) is serving."""
    hub = connection_hub(request)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > wire.MAX_REQUEST_BYTES:
            return Response("preview request body too large", status_code=413)
        body.extend(chunk)

    async def disconnected():
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    waiting = asyncio.create_task(
        hub.request_stream(
            topic_id,
            seat,
            method=request.method,
            path=_upstream_path(request),
            headers=_app_headers(request),
            body=bytes(body),
            instance=instance,
            typed=hub is not preview_hub,
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
                # The requester left before we had an answer — a page reload
                # cancels its in-flight fetches. Nobody is here to render a
                # waiting page, and the app may be perfectly fine, so this is
                # not the "app unreachable" case the interstitial speaks for.
                return Response(status_code=404, content=b"preview unavailable")
            try:
                upstream = await waiting
            except PreviewAdmissionError as exc:
                return preview_unavailable_response(
                    state=exc.state,
                    navigation=is_document_navigation(request.method, request.headers),
                    accept_language=request.headers.get("accept-language"),
                )
        finally:
            gone.cancel()
            if not waiting.done():
                waiting.cancel()
            await asyncio.gather(waiting, gone, return_exceptions=True)
        if upstream is None:
            # No response from the app at all: the tunnel dropped, the app is
            # restarting, the stream timed out. The legacy path answered a bare
            # 404 here, which a navigation renders as a white frame. Both paths
            # now say the same transient thing; a navigation also gets the page
            # that keeps trying.
            return preview_unavailable_response(
                state=UNAVAILABLE,
                navigation=is_document_navigation(request.method, request.headers),
                accept_language=request.headers.get("accept-language"),
            )
        kept = _response_headers(upstream.headers)
        media_type = next((v for k, v in kept if k.lower() == "content-type"), None)
        inject = _injects(request.method, upstream.status, kept)
        response = _PreviewStreamingResponse(
            upstream, _with_runtime(upstream.iter_bytes()) if inject else None
        )
        # Keep app sessions without allowing an app to replace preview auth.
        for name, value in kept:
            if inject and name.lower() == "content-length":
                # 插进去的那一行改了长度；不写长度，交给分块传输。
                continue
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


async def relay_ws(
    websocket: WebSocket, topic_id: uuid.UUID, seat: str, *, instance: str | None = None
) -> None:
    """Pump the authorized preview's HMR socket without holding a DB session."""
    hub = connection_hub(websocket)
    stream = hub.open_stream(topic_id, seat)
    if stream is None:
        await websocket.close(code=1011)
        return
    try:
        if instance and "instance-v1" not in stream._machine.capabilities:
            await websocket.close(code=1008)
            return
        await stream.send(
            wire.OP_WS_OPEN,
            wire.encode_meta(
                {
                    "path": _upstream_path(websocket),
                    **({"instance": instance} if instance else {}),
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
                text = message.get("text")
                payload = (
                    bytes([wire.WS_BINARY]) + data
                    if data is not None
                    else bytes([wire.WS_TEXT]) + text.encode()
                    if text is not None
                    else b""
                )
                if len(payload) > wire.MAX_WS_MESSAGE_BYTES:
                    await browser.close(code=1009, reason="preview message too large")
                    return
                if payload:
                    await stream.send(wire.OP_WS_MSG, payload)
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
                if stream.maintenance:
                    code, reason = 1012, "preview owner maintenance"
                elif stream.close_metadata:
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
        with anyio.CancelScope(shield=True):
            await asyncio.gather(up, down, return_exceptions=True)
        await stream.aclose()
