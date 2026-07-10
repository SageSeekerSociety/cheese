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

Read-only: ttyd runs with ``-R``, so the pane is a pure mirror — keystrokes in
the browser never reach the container.

Authorization: there is no browser user-auth in this MVP (the whole read API is
open), so the gate is the same as every other topic route — the topic must exist
— PLUS the proxy is scoped to THIS topic's container (the ``docker port`` lookup
uses the topic id), so a token/URL for one topic can never reach another's pane.
"""

import uuid
from typing import Annotated

import httpx
import websockets
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.response import ok
from app.core.config import settings
from app.core.db import get_db
from app.domain.agent.tmux_provider import ttyd_endpoint
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/api/topics", tags=["terminal"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Hop-by-hop headers a proxy must not forward (RFC 7230 §6.1) plus length/type,
# which the Response recomputes from the body it actually sends.
_DROP_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-encoding",
    "content-length",
    "te",
    "trailer",
    "upgrade",
}


def _live_endpoint(topic_id: uuid.UUID) -> str | None:
    """`127.0.0.1:<port>` of the topic's ttyd, or None when the terminal isn't
    available (wrong backend, or the container is down / has no published port)."""
    if settings.agent_backend != "tmux":
        return None
    return ttyd_endpoint(topic_id)


@router.get("/{topic_id}/terminal")
async def terminal_status(topic_id: uuid.UUID, db: DbSession) -> dict:
    """Whether this topic has an embeddable live terminal, and where to load it.

    ``available`` is true only under the tmux backend with the topic's container
    up and 7681 published; otherwise the frontend falls back to the worklog view.
    ``url`` is the iframe source — the proxy base below (trailing slash matters:
    ttyd derives ``/ws`` from the page path)."""
    await TopicService(db).get_or_404(topic_id)  # topic 访问校验
    backend = settings.agent_backend
    endpoint = _live_endpoint(topic_id)
    if endpoint is None:
        return ok({"available": False, "backend": backend})
    return ok(
        {
            "available": True,
            "backend": backend,
            "url": f"/api/topics/{topic_id}/terminal/live/",
        }
    )


@router.api_route("/{topic_id}/terminal/live", methods=["GET"])
@router.api_route("/{topic_id}/terminal/live/{path:path}", methods=["GET"])
async def terminal_proxy_http(
    topic_id: uuid.UUID, request: Request, path: str = ""
) -> Response:
    """Reverse-proxy a ttyd HTTP request (the xterm.js page, ``/token``, assets).
    Scoped to the topic's own container, so it can't reach another topic."""
    endpoint = _live_endpoint(topic_id)
    if endpoint is None:
        return Response(status_code=404, content=b"terminal unavailable")
    url = f"http://{endpoint}/{path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        upstream = await client.get(url, params=request.query_params)
    headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


@router.websocket("/{topic_id}/terminal/live/ws")
async def terminal_proxy_ws(websocket: WebSocket, topic_id: uuid.UUID) -> None:
    """Reverse-proxy the ttyd WebSocket. ttyd speaks the ``tty`` subprotocol; we
    negotiate it on both legs and pump frames transparently (binary pane output
    upstream→browser, control/resize JSON browser→upstream). Read-only pane, so
    browser input is inert, but we still forward it (harmless ttyd control)."""
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
            await _pump(websocket, upstream)
    except (OSError, websockets.WebSocketException):
        # Container gone / ttyd not up yet — close cleanly if we ever accepted.
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass


async def _pump(browser: WebSocket, upstream: "websockets.ClientConnection") -> None:
    """Run both directions until either side closes, then tear the other down."""
    import asyncio

    async def browser_to_upstream() -> None:
        try:
            while True:
                msg = await browser.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                data = msg.get("bytes")
                if data is not None:
                    await upstream.send(data)
                    continue
                text = msg.get("text")
                if text is not None:
                    await upstream.send(text)
        except (WebSocketDisconnect, websockets.WebSocketException):
            return

    async def upstream_to_browser() -> None:
        try:
            async for frame in upstream:
                if isinstance(frame, bytes):
                    await browser.send_bytes(frame)
                else:
                    await browser.send_text(frame)
        except (WebSocketDisconnect, websockets.WebSocketException, RuntimeError):
            return

    t_up = asyncio.create_task(browser_to_upstream())
    t_down = asyncio.create_task(upstream_to_browser())
    _, pending = await asyncio.wait(
        {t_up, t_down}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await upstream.close()
    try:
        await browser.close()
    except RuntimeError:
        pass
