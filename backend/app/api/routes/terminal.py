"""施工现场 real-terminal proxy (spec §7.1, docs/tmux-backend-spike.md).

When the interactive/tmux backend is active (``AGENT_BACKEND=tmux``), every topic
container runs a read-only ``ttyd`` mirror of the `claude` tmux pane on port 7681
(published to a random ``127.0.0.1:<port>``). The browser can't reach that host
port directly, so this module reverse-proxies it under the topic's API namespace:

  * ``GET  /api/topics/{id}/terminal``           → status ``{available, backend, url?}``
  * ``GET  /api/topics/{id}/terminal/live[/…]``   → ttyd HTTP (the xterm.js page,
                                                    ``/token``, assets)
  * ``WS   /api/topics/{id}/terminal/live/ws``     → ttyd WebSocket (subprotocol
                                                    ``tty``)

ttyd's client builds its WebSocket/token URLs RELATIVE to ``location.pathname``
(stripped of trailing slashes) + ``/ws`` / ``/token`` — verified against the
1.7.7 bundle — so serving its page at ``…/terminal/live/`` makes those land back
on this proxy with no rewriting. The frontend iframes ``…/terminal/live/`` and
Vite (or the reverse proxy) forwards both HTTP and the WS upgrade to us.

**This path must reach us un-rewritten.** Both gateways front the API by stripping
one ``/api`` (nginx ``proxy_pass http://backend:8081/``), which would turn the
iframe's URL into ``/topics/…`` and 404 it — so ``frontend/nginx.conf`` and
``vite.config.ts`` each carry an explicit no-strip exception for this prefix. Move
the routes and those two must move with them.

Read-only: ttyd runs with ``-R``, so the pane is a pure mirror — keystrokes in
the browser never reach the container.

Authorization: a member/owner of the topic's project (``proxy.may_view_topic``).
The credential rides as ``?token=`` because a browser can set no header on an
iframe or a WebSocket, and the page load re-issues it as a path-scoped cookie so
ttyd's own sub-requests authenticate too. The proxy is additionally scoped to THIS
topic's container (the ``docker port`` lookup uses the topic id), so a token/URL
for one topic can never reach another's pane.
"""

import uuid
from typing import Annotated

import websockets
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket

from app.api import proxy
from app.api.response import ok
from app.core.config import settings
from app.core.db import get_db
from app.domain.agent.device_hub import device_hub
from app.domain.agent.tmux_provider import ttyd_endpoint
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/api/topics", tags=["terminal"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Sub-request credential for the iframe's own fetches (ttyd's ``/token``, assets).
# Scoped to this topic's ``…/terminal`` path — see app.api.proxy.
COOKIE_NAME = "cheesex_proxy"


def _cookie_path(request: Request) -> str:
    """The browser-visible ``…/terminal`` prefix of this request, so the cookie
    covers the pane's sub-requests and nothing else. Derived from the live path
    rather than hard-coded, so it stays right behind any external prefix."""
    path = request.url.path
    marker = "/terminal"
    idx = path.find(marker)
    return path[: idx + len(marker)] if idx != -1 else path


def _device_screen_id(topic_id: uuid.UUID) -> str | None:
    """The live screen a device is running this topic in, if any.

    A remote machine has no ttyd for us to proxy — it is behind NAT — so its pane
    travels over the link the device already dialled out on. The terminal exists
    either way; only the transport differs, and this endpoint is what tells the
    frontend which one to open."""
    for screen in device_hub.all_online_screens():
        if screen.topic_id == topic_id:
            return screen.sid
    return None


def _live_endpoint(topic_id: uuid.UUID) -> str | None:
    """`127.0.0.1:<port>` of the topic's ttyd, or None when the terminal isn't
    available (wrong backend, or the container is down / has no published port)."""
    if settings.agent_backend != "tmux":
        return None
    return ttyd_endpoint(topic_id)


@router.get("/{topic_id}/terminal")
async def terminal_status(topic_id: uuid.UUID, request: Request, db: DbSession) -> dict:
    """Whether this topic has an embeddable live terminal, and where to load it.

    ``available`` must answer "will an iframe of ``url`` actually show a pane?",
    because that is the only question the frontend asks it — a true here means the
    drawer replaces the 施工记录 timeline with the embed. So it takes all three
    reasons the embed could fail, not just the easy one:

      * the caller has no credential → the proxy would 404 the iframe;
      * the wrong backend / the container is down → no endpoint at all;
      * the port is published but nothing answers on it → a white box.

    Reporting availability off the port mapping alone (the old behavior) is what
    left users staring at a blank frame with no way back to the timeline.
    """
    await TopicService(db).get_or_404(topic_id)  # topic 访问校验
    backend = settings.agent_backend
    if not await proxy.may_view_topic(db, topic_id, request, COOKIE_NAME):
        return ok({"available": False, "backend": backend})
    sid = _device_screen_id(topic_id)
    if sid is not None:
        return ok(
            {
                "available": True,
                "backend": backend,
                "interactive": True,
                "ws": f"/connector/session/{sid}/screen",
            }
        )
    endpoint = _live_endpoint(topic_id)
    if endpoint is None or not await proxy.probe(endpoint):
        return ok({"available": False, "backend": backend})
    return ok(
        {
            "available": True,
            "backend": backend,
            "interactive": True,
            "url": f"/api/topics/{topic_id}/terminal/live/",
        }
    )


@router.api_route("/{topic_id}/terminal/live", methods=["GET"])
@router.api_route("/{topic_id}/terminal/live/{path:path}", methods=["GET"])
async def terminal_proxy_http(
    topic_id: uuid.UUID, request: Request, db: DbSession, path: str = ""
) -> Response:
    """Reverse-proxy a ttyd HTTP request (the xterm.js page, ``/token``, assets).
    Scoped to the topic's own container, so it can't reach another topic."""
    # 404 rather than 403: an unauthorized caller learns nothing about whether
    # the topic or its terminal exists.
    if not await proxy.may_view_topic(db, topic_id, request, COOKIE_NAME):
        return Response(status_code=404, content=b"terminal unavailable")
    endpoint = _live_endpoint(topic_id)
    if endpoint is None:
        return Response(status_code=404, content=b"terminal unavailable")
    response = await proxy.forward(endpoint, path, request)
    return proxy.attach_cookie(
        response, request, cookie_name=COOKIE_NAME, cookie_path=_cookie_path(request)
    )


@router.websocket("/{topic_id}/terminal/live/ws")
async def terminal_proxy_ws(
    websocket: WebSocket, topic_id: uuid.UUID, db: DbSession
) -> None:
    """Reverse-proxy the ttyd WebSocket. ttyd speaks the ``tty`` subprotocol; we
    negotiate it on both legs and pump frames transparently (binary pane output
    upstream→browser, control/resize JSON browser→upstream). Read-only pane, so
    browser input is inert, but we still forward it (harmless ttyd control)."""
    allowed = await proxy.may_view_topic(db, topic_id, websocket, COOKIE_NAME)
    # Release the authz read-transaction before the (long-lived) pump. A get_db
    # session injected into a WebSocket route is only finalized when the socket
    # closes, so leaving it open parks it `idle in transaction` for the whole
    # terminal session — the #356 footgun: an idle-in-txn read lock blocked
    # device/topic-table migrations (ACCESS EXCLUSIVE) until they timed out.
    await db.commit()
    if not allowed:
        await websocket.close(code=1008)
        return
    endpoint = _live_endpoint(topic_id)
    if endpoint is None:
        # Reject the handshake (no accept) — the client sees a failed upgrade.
        await websocket.close(code=1011)
        return
    upstream_url = f"ws://{endpoint}/ws"
    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=[websockets.Subprotocol("tty")],
            open_timeout=15.0,
        ) as upstream:
            # Mirror ttyd's negotiated subprotocol back to the browser so xterm's
            # `new WebSocket(url, ["tty"])` handshake completes.
            sub = upstream.subprotocol or "tty"
            await websocket.accept(subprotocol=sub)
            await proxy.pump(websocket, upstream)
    except (OSError, websockets.WebSocketException):
        # Container gone / ttyd not up yet — close cleanly if we ever accepted.
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
