"""运行环境预览 reverse proxy — spec §7.1 的「升级为经后端的反向代理」.

Every topic container publishes the conventional app port (``$CHEESE_APP_PORT``,
3000) to a random host port bound to **127.0.0.1**. 芝士 starts whatever server
the project needs there and declares it with ``cheese serve``.

``/preview`` used to hand the browser that host address verbatim
(``http://127.0.0.1:<host-port>``). It resolves only for someone running the whole
platform on their own laptop: for every remote user the loopback is their OWN
machine, so the panel showed a white frame with no error. The pane in 施工现场
already solved this by reverse-proxying through the backend; this is the same
route for the app:

  * ``GET/POST/… /api/topics/{id}/app[/…]`` → the container's app port
  * ``WS         /api/topics/{id}/app/…``    → same, for dev-server HMR sockets

**Sub-path caveat.** The app is served under ``/api/topics/{id}/app/``, so a page
that asks for its assets by *root-absolute* path (``/assets/x.js``,
``/@vite/client``) would miss. Root-absolute URLs in proxied HTML are rewritten
onto the prefix, which covers static servers and a plain dev-server index; an app
whose JS builds root-absolute URLs at runtime still needs to be started under a
matching base (vite: ``--base=/api/topics/<id>/app/``).

Authorization and the sub-request cookie work exactly as in ``routes/terminal.py``
— see ``app.api.proxy``. The same "don't strip ``/api``" gateway exception applies
to this prefix (``frontend/nginx.conf``, ``vite.config.ts``).
"""

import re
import uuid
from typing import Annotated

import websockets
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket

from app.api import proxy
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.domain.workspace import service as ws

router = APIRouter(prefix="/topics", tags=["preview"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Shared with the terminal proxy: one name, but each is path-scoped to its own
# ``…/terminal`` or ``…/app`` prefix, so they never see each other's cookie.
COOKIE_NAME = "cheesex_proxy"

# ``src="/x"`` / ``href='/x'`` / ``action="/x"`` — but never ``//host`` (protocol
# relative) and never an already-absolute URL.
_ROOT_ABSOLUTE_ATTR = re.compile(r"""(\s(?:src|href|action)\s*=\s*["'])/(?!/)""")


#: What the BROWSER asks for. Since the 2.0 prefix was flattened (#370 step 2)
#: this is no longer the same string as the route's own path (`/topics/…/app`):
#: it is the gateway mount plus that path. Both the cookie scope and the
#: root-absolute URL rewriting below have to speak the browser's language — the
#: page resolves its assets against `location.pathname`, which never saw the
#: strip — so this constant must NOT be flattened along with the route.
def _prefix(topic_id: uuid.UUID) -> str:
    return proxy.browser_path(f"/topics/{topic_id}/app")


def _rewrite_html(body: bytes, prefix: str) -> bytes:
    """Point the page's root-absolute asset URLs at the proxy prefix."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    return _ROOT_ABSOLUTE_ATTR.sub(rf"\g<1>{prefix}/", text).encode("utf-8")


@router.get("/{topic_id}/app-session")
async def app_session(
    topic_id: uuid.UUID, request: Request, response: Response, db: DbSession
) -> dict:
    """Mint the cookie the preview iframe will authenticate with, then say ready.

    The iframe deliberately carries NO ``?token=``. Unlike the terminal — whose
    page is ttyd, our own fixed bundle — the app frame renders whatever the agent
    chose to serve, and a query string is readable by that page's own JavaScript
    (``location.search``) even inside a sandboxed frame. That would hand arbitrary
    agent-authored code the viewer's session token. An HttpOnly, path-scoped
    cookie is not readable by it, and same-origin requests carry it on their own.

    The frontend calls this (with its normal ``Authorization`` header) right
    before it sets the iframe's src.
    """
    if not await proxy.may_view_topic(db, topic_id, request, COOKIE_NAME):
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
    """Reverse-proxy one request to the topic's running app."""
    # 404 rather than 403: an unauthorized caller learns nothing about whether the
    # topic or its app exists.
    if not await proxy.may_view_topic(db, topic_id, request, COOKIE_NAME):
        return Response(status_code=404, content=b"preview unavailable")
    endpoint = ws.app_endpoint(topic_id)
    if endpoint is None:
        return Response(status_code=404, content=b"preview unavailable")
    response = await proxy.forward(endpoint, path, request)
    if (response.media_type or "").startswith("text/html"):
        response = Response(
            content=_rewrite_html(bytes(response.body), _prefix(topic_id)),
            status_code=response.status_code,
            headers={
                k: v
                for k, v in response.headers.items()
                if k.lower() not in {"content-length", "content-type"}
            },
            media_type=response.media_type,
        )
    return proxy.attach_cookie(
        response, request, cookie_name=COOKIE_NAME, cookie_path=_prefix(topic_id)
    )


@router.websocket("/{topic_id}/app/{path:path}")
async def app_proxy_ws(
    websocket: WebSocket, topic_id: uuid.UUID, db: DbSession, path: str = ""
) -> None:
    """Reverse-proxy a WebSocket to the topic's app — dev servers push HMR over
    one, and without it the page reloads forever trying to reconnect."""
    allowed = await proxy.may_view_topic(db, topic_id, websocket, COOKIE_NAME)
    # Release the authz read-transaction before the (long-lived) pump. A get_db
    # session injected into a WebSocket route is only finalized when the socket
    # closes, so leaving it open parks it `idle in transaction` for the whole
    # app-preview session — the #356 footgun: an idle-in-txn read lock blocked
    # device/topic-table migrations (ACCESS EXCLUSIVE) until they timed out.
    await db.commit()
    if not allowed:
        await websocket.close(code=1008)
        return
    endpoint = ws.app_endpoint(topic_id)
    if endpoint is None:
        await websocket.close(code=1011)
        return
    # Offer whatever subprotocol the client asked for (vite HMR uses ``vite-hmr``).
    requested = websocket.headers.get("sec-websocket-protocol", "")
    subprotocols = [
        websockets.Subprotocol(p.strip()) for p in requested.split(",") if p.strip()
    ]
    try:
        async with websockets.connect(
            f"ws://{endpoint}/{path}",
            subprotocols=subprotocols or None,
            open_timeout=15.0,
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)
            await proxy.pump(websocket, upstream)
    except (OSError, websockets.WebSocketException):
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
