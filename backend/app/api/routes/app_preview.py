"""运行环境预览: the app the agent started on its machine, shown in the browser.

Two ends of one thing, so they live together.

**The machine's end** (``WS /preview/tunnel``) is dialled OUT by the helper
``cheese serve`` starts, authenticated by the same scoped cheese token the turn
already carries. Nothing is ever dialled INTO a machine: every one of them is
someone else's, behind NAT, with zero inbound ports — which is exactly why the
old preview (a port the backend's own docker had published on its own host) has
nothing left to read.

**The browser's end** (``/topics/{id}/app…``) reverse-proxies through that
tunnel, HTTP and WebSocket alike, so a dev server's HMR socket connects too.

**Sub-path caveat.** The app is served under ``/api/topics/{id}/app/``, so a page
that asks for its assets by *root-absolute* path (``/assets/x.js``,
``/@vite/client``) would miss. Root-absolute URLs in proxied HTML are rewritten
onto the prefix, which covers static servers and a plain dev-server index; an app
whose JS builds root-absolute URLs at runtime still needs to be started under a
matching base (vite: ``--base=$CHEESE_APP_BASE``).

**Who may look.** A member or owner of the topic's project — ``may_view_topic``,
the same gate the 现场 terminal answers with. The id in the URL names a PLACE and
the roster belongs to its ROOM, which for a thread are two different uuids;
``_viewer_place`` is the single spot that keeps them apart. That is deliberately
not a new boundary: whoever can open this can already TYPE into a shell on that machine
through the 现场 viewer, so a read-only view of one loopback port on it grants
nothing further. What the preview must never do is hand the page itself a
credential, and it does not: the iframe carries no ``?token=`` (agent-authored
JavaScript can read ``location.search`` even sandboxed) — only an HttpOnly cookie
scoped to this one path.
"""

import asyncio
import re
import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api import proxy
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewStream, preview_hub
from app.domain.room_task.place import Place, PlaceResolver

router = APIRouter(prefix="/topics", tags=["preview"])
tunnel_router = APIRouter(prefix="/preview", tags=["preview"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Shared with the terminal proxy: one name, but each is path-scoped to its own
# prefix, so they never see each other's cookie.
COOKIE_NAME = "cheesex_proxy"

# ``src="/x"`` / ``href='/x'`` / ``action="/x"`` — but never ``//host`` (protocol
# relative) and never an already-absolute URL.
_ROOT_ABSOLUTE_ATTR = re.compile(r"""(\s(?:src|href|action)\s*=\s*["'])/(?!/)""")

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


def _prefix(topic_id: uuid.UUID) -> str:
    """What the BROWSER asks for — the gateway mount plus this route's own path.
    The page resolves its assets against ``location.pathname``, which never saw
    the gateway's strip, so both the cookie scope and the URL rewriting below
    have to speak the browser's language rather than the route's."""
    return proxy.browser_path(f"/topics/{topic_id}/app")


def _rewrite_html(body: bytes, prefix: str) -> bytes:
    """Point the page's root-absolute asset URLs at the proxy prefix."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    return _ROOT_ABSOLUTE_ATTR.sub(rf"\g<1>{prefix}/", text).encode("utf-8")


def _upstream_path(path: str, conn: Request | WebSocket) -> str:
    """What to ask the app for: the sub-path and the caller's own query string,
    minus our credential.

    Dropping ``token`` is the same rule the cookie exists for. The page is
    written by the agent, and a session token that reaches it — in a query string
    it can read back, or in a request its own server logs — is the one thing this
    surface must never hand over. A caller may still present one (that is how an
    iframe authenticates before the cookie lands); it just stops here.
    """
    kept = [(k, v) for k, v in conn.query_params.multi_items() if k != "token"]
    query = urlencode(kept)
    return f"/{path}?{query}" if query else f"/{path}"


# --- the machine's end ---------------------------------------------------------


class _WebSocketPreviewTransport:
    """Adapts a live ``fastapi.WebSocket`` to the hub's ``PreviewTransport``."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_bytes(self, data: bytes) -> None:
        await self._websocket.send_bytes(data)

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


async def _viewer_place(
    db: AsyncSession, topic_id: uuid.UUID, conn: Request | WebSocket
) -> Place | None:
    """The place this id names, if the caller may look inside it.

    Two ids, and for a thread they are not the same id: the app, the tunnel and
    the cookie all belong to the PLACE (a thread serves its own app under its
    own id), while who may look is the ROOM's roster — the only roster there is.
    ``may_view_topic`` finds its roster through a ``topics`` row, so handing it a
    thread's id is not a 403 but a 404 from underneath: work that plainly exists,
    with a running app, reported as no such topic. A room never shows it, the two
    ids being one there.

    "No such place" and "not allowed" both come back as None on purpose. A topic
    id is a uuid and nothing more, so answering the two apart would turn this
    route into an oracle for whether an id names anything — which is the reason
    the callers below reply 404 to an unauthorized caller in the first place.
    """
    place = await PlaceResolver(db).resolve(topic_id)
    if place is None:
        return None
    if not await proxy.may_view_topic(db, place.room_id, conn, COOKIE_NAME):
        return None
    return place


@router.get("/{topic_id}/app-session")
async def app_session(
    topic_id: uuid.UUID, request: Request, response: Response, db: DbSession
) -> dict:
    """Mint the cookie the preview iframe will authenticate with, then say ready.

    The iframe deliberately carries NO ``?token=``. Unlike the terminal, the app
    frame renders whatever the agent chose to serve, and a query string is
    readable by that page's own JavaScript (``location.search``) even inside a
    sandboxed frame — which would hand arbitrary agent-authored code the viewer's
    session token. An HttpOnly, path-scoped cookie is not readable by it, and
    same-origin requests carry it on their own.

    The frontend calls this (with its normal ``Authorization`` header) right
    before it sets the iframe's src.
    """
    if await _viewer_place(db, topic_id, request) is None:
        raise NotFoundError("没有可预览的应用")
    # Hard-coded rather than derived from this request's path: THIS route is
    # reached through the gateway's `/api`-stripping prefix while the iframe is
    # not, so only the proxy's own public path is the right scope.
    proxy.attach_cookie(
        response, request, cookie_name=COOKIE_NAME, cookie_path=_prefix(topic_id)
    )
    return ok({"ready": True})


@router.api_route(
    "/{topic_id}/app", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
)
@router.api_route(
    "/{topic_id}/app/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
)
async def app_proxy_http(
    topic_id: uuid.UUID, request: Request, db: DbSession, path: str = ""
) -> Response:
    """Reverse-proxy one request to the topic's running app, over its tunnel."""
    # 404 rather than 403: an unauthorized caller learns nothing about whether the
    # topic or its app exists.
    if await _viewer_place(db, topic_id, request) is None:
        return Response(status_code=404, content=b"preview unavailable")
    upstream = await preview_hub.request(
        topic_id,
        method=request.method,
        path=_upstream_path(path, request),
        headers=proxy.forwardable_request_headers(request),
        body=await request.body(),
    )
    if upstream is None:
        return Response(status_code=404, content=b"preview unavailable")
    kept = [(k, v) for k, v in upstream.headers if k.lower() not in proxy.DROP_HEADERS]
    media_type = next((v for k, v in kept if k.lower() == "content-type"), None)
    body = upstream.body
    if (media_type or "").startswith("text/html"):
        body = _rewrite_html(body, _prefix(topic_id))
    response = Response(content=body, status_code=upstream.status)
    # Appended one at a time rather than handed over as a dict: an app may send
    # the same header twice (Set-Cookie is the usual one) and a dict silently
    # keeps only the last.
    for name, value in kept:
        if name.lower() != "content-type":
            response.headers.append(name, value)
    if media_type:
        response.headers["content-type"] = media_type
    # Assignment, not append: the app may have sent one of its own, and a
    # response carrying this header twice is rejected outright. The frame is
    # sandboxed without `allow-same-origin`, so the browser gives it an opaque
    # origin and stamps `Origin: null` on everything it fetches — including
    # `<script type="module">`, which unlike a classic script tag is always
    # fetched in CORS mode. Absent this header the browser discards a perfectly
    # good 200 on arrival and a module-script app never runs a line. Opening it
    # wide costs nothing: an opaque origin holds no cookie and no storage to
    # leak, so there is nothing here the sandbox was not already withholding.
    response.headers["access-control-allow-origin"] = "*"
    return proxy.attach_cookie(
        response, request, cookie_name=COOKIE_NAME, cookie_path=_prefix(topic_id)
    )


@router.websocket("/{topic_id}/app/{path:path}")
async def app_proxy_ws(
    websocket: WebSocket, topic_id: uuid.UUID, db: DbSession, path: str = ""
) -> None:
    """Reverse-proxy a WebSocket to the topic's app — dev servers push HMR over
    one, and without it the page reloads forever trying to reconnect."""
    allowed = await _viewer_place(db, topic_id, websocket) is not None
    # Release the authz read-transaction before the (long-lived) pump. A get_db
    # session injected into a WebSocket route is only finalized when the socket
    # closes, so leaving it open parks it `idle in transaction` for the whole
    # preview session — the #356 footgun: an idle-in-txn read lock blocked
    # device/topic-table migrations (ACCESS EXCLUSIVE) until they timed out.
    await db.commit()
    if not allowed:
        await websocket.close(code=1008)
        return
    stream = preview_hub.open_stream(topic_id)
    if stream is None:
        await websocket.close(code=1011)
        return
    try:
        await stream.send(
            wire.OP_WS_OPEN,
            wire.encode_meta(
                {
                    "path": _upstream_path(path, websocket),
                    "headers": [
                        [k, v]
                        for k, v in proxy.forwardable_request_headers(websocket)
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
    except (TimeoutError, ValueError, RuntimeError, WebSocketDisconnect):
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
        except (WebSocketDisconnect, RuntimeError):
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
    _, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await stream.send(wire.OP_CLOSE)
