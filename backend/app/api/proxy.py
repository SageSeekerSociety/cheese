"""Shared plumbing for the two "let the browser see inside the container" proxies.

Both 施工现场's live terminal (``routes/terminal.py``) and 运行环境预览's running
app (``routes/app_preview.py``) have the same shape: something in the topic's
container listens on a host port bound to **127.0.0.1**, which a remote browser
can never reach, so the backend reverse-proxies it under the topic's API
namespace. Same authorization question, same header hygiene, same WebSocket pump
— so it lives here once instead of being copy-pasted per feature.

Credential: a browser cannot set an ``Authorization`` header on an ``<iframe>``
or a ``WebSocket``, so the session token rides as ``?token=``. The iframe's own
*sub*-requests (assets, ttyd's ``/token``, HMR) don't inherit that query string,
so a successful page load also drops a **path-scoped** cookie; everything under
that path then authenticates itself. Scoping the cookie to the topic's own proxy
path is what keeps it from becoming an ambient credential for the rest of the API.
"""

import uuid

import httpx
import websockets
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.core.tokens import verify_session_token
from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.services import TopicService
from app.domain.user.repositories import UserRepository

# Hop-by-hop headers a proxy must not forward (RFC 7230 §6.1) plus length/type,
# which the Response recomputes from the body it actually sends.
DROP_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-encoding",
    "content-length",
    "te",
    "trailer",
    "upgrade",
}

# How long the path-scoped sub-request cookie stays valid. Short: it only has to
# outlive one open drawer, and it is re-issued on every page load.
COOKIE_TTL_S = 8 * 3600


def credential(conn: Request | WebSocket, cookie_name: str) -> str | None:
    """The caller's session token: ``Authorization: Bearer`` (normal API calls),
    ``?token=`` (the iframe/WebSocket the frontend builds), or the path-scoped
    cookie the page load dropped (the iframe's own sub-requests)."""
    header = conn.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            return token
    return conn.query_params.get("token") or conn.cookies.get(cookie_name) or None


async def _resolve_handle(session: AsyncSession, token: str) -> str | None:
    """Handle behind a session token, or None. main-minted tokens put the int user
    id in ``sub`` and the username in ``handle``; legacy cheesex ones are
    handle-only — 2.0 membership is keyed by handle, so both must land on one."""
    claims = verify_session_token(token)
    if claims is None:
        return None
    handle = claims["handle"] or claims["sub"]
    if not handle:
        return None
    if str(handle).isdigit():
        user = await UserRepository(session).get_by_id(int(handle))
        return getattr(user, "handle", None)
    return str(handle)


async def may_view_topic(
    session: AsyncSession,
    topic_id: uuid.UUID,
    conn: Request | WebSocket,
    cookie_name: str,
) -> bool:
    """Whether this caller may look inside the topic's container.

    A topic id is a UUID, but that is obscurity, not authorization: the pane (and
    the running app) show whatever the agent is doing, so anyone who learns an id
    would otherwise get a read of the project's contents. Gate is the same as the
    device viewer's: a logged-in member or owner of the topic's project.
    """
    token = credential(conn, cookie_name)
    if not token:
        return False
    handle = await _resolve_handle(session, token)
    if not handle:
        return False
    topic = await TopicService(session).get_or_404(topic_id)
    project_id = topic.project_id
    if project_id is None:
        return False
    if await MemberRepository(session).get(project_id=project_id, user_handle=handle):
        return True
    project = await ProjectRepository(session).get(project_id)
    return project is not None and project.owner_handle == handle


def attach_cookie(
    response: Response, request: Request, *, cookie_name: str, cookie_path: str
) -> Response:
    """Re-issue the sub-request cookie from the credential this request proved.

    Only ever mirrors a token the caller already presented, so it grants nothing
    new — it just carries the same proof to requests the browser makes on its own
    (assets, ``/token``, HMR) which cannot carry the query string.
    """
    token = credential(request, cookie_name)
    if token:
        response.set_cookie(
            cookie_name,
            token,
            path=cookie_path,
            max_age=COOKIE_TTL_S,
            httponly=True,
            samesite="lax",
        )
    return response


async def probe(endpoint: str, *, timeout: float = 2.0) -> bool:
    """Whether something actually answers HTTP on ``host:port`` right now.

    A published docker port is NOT the same as a live server: the container can be
    up with its process dead, and reporting ``available`` off the port mapping
    alone is what made the frontend embed an iframe that could only ever render a
    white box. Any failure is a "no" — this decides between a real embed and an
    honest fallback, never between working and broken.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"http://{endpoint}/")
    except (httpx.HTTPError, OSError):
        return False
    return resp.status_code < 500


async def forward(endpoint: str, path: str, request: Request) -> Response:
    """Proxy one upstream request, minus the hop-by-hop headers.

    Redirects are passed through rather than followed: the browser must see the
    3xx so it re-resolves the ``Location`` against the proxy path, not against the
    container's own loopback address.
    """
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        upstream = await client.request(
            request.method,
            f"http://{endpoint}/{path}",
            params=request.query_params,
            headers=_forwardable_request_headers(request),
            content=await request.body() if request.method != "GET" else None,
        )
    headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in DROP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


def _forwardable_request_headers(request: Request) -> dict[str, str]:
    """Pass the browser's content negotiation upstream (a dev server serves very
    different bytes for ``Accept: text/html`` vs a module request) while dropping
    hop-by-hop headers, our own Host, and the cookie (upstream has no business
    seeing the session token). ``accept-encoding`` goes too: httpx decodes the
    body it returns but we re-send it under the upstream's headers, so asking for
    a compressed body only risks a content-encoding mismatch for zero gain."""
    drop = DROP_HEADERS | {
        "host",
        "cookie",
        "authorization",
        "content-length",
        "accept-encoding",
    }
    return {k: v for k, v in request.headers.items() if k.lower() not in drop}


async def pump(browser: WebSocket, upstream: "websockets.ClientConnection") -> None:
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
    _, pending = await asyncio.wait({t_up, t_down}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await upstream.close()
    try:
        await browser.close()
    except RuntimeError:
        pass
