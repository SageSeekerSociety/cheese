"""An open page built for an older API is told so, instead of being answered
in a shape it no longer speaks.

A deploy replaces the backend at once, while a page opened before it keeps
running the frontend it loaded until its next full load (``frontend/src/pwa.ts``
waits for the next navigation). A change that renames a field or reshapes a
request changes both halves in one pull request, with no compatibility path
(AGENTS.md), so a page left open on a room or a task then sends the old shape
to the new backend: a 422, or a field the backend silently ignores.

The API generation is one integer, ``backend/api-generation``, raised by every
change that breaks an older frontend (the ``api`` job of Required CI refuses a
breaking change that leaves it alone: ``.github/scripts/api_compat.py``). The
web build bakes the same file in and sends it on every request
(``X-Cheese-Api-Gen``; ``?api_gen=`` on a WebSocket, where a browser cannot set
a header). A request carrying a lower number is refused here before any route
sees it — ``409`` named ``ClientOutdated`` over HTTP, close code ``4409`` on a
WebSocket, accepted first so the page can read the code — and the page asks
to be refreshed onto the current build (``frontend/src/lib/apiGeneration.ts``).

A request that carries no number is never refused: the CLI, the desktop app's
own calls, MCP, the connector, scripts and anything else that is not the web
build send none, and they are not loaded from a deploy.
"""

from pathlib import Path

from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import format_error_response
from app.core.sentences import say

#: ``backend/`` in a checkout, ``/app`` in the image (backend/Dockerfile copies it).
GENERATION_FILE = Path(__file__).resolve().parents[2] / "api-generation"

#: The generation this backend speaks; a page below it is refused.
CURRENT = int(GENERATION_FILE.read_text(encoding="utf-8").strip())

HEADER = "x-cheese-api-gen"
QUERY = "api_gen"
#: 4000-4999 is the application's own range; 409 echoes the HTTP refusal.
CLOSE_CODE = 4409


def _sent(scope: Scope) -> int | None:
    raw = Headers(scope=scope).get(HEADER)
    if raw is None and scope["type"] == "websocket":
        raw = QueryParams(scope.get("query_string", b"")).get(QUERY)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        # Only the web build sends it, and it sends an integer. Anything else
        # is not a page we know how to send to a newer build.
        return None


class OutdatedClientGuard:
    """Pure ASGI: refuses a request or WebSocket from a page whose API
    generation is below ``CURRENT``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            sent = _sent(scope)
            if sent is not None and sent < CURRENT:
                await _refuse(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "websocket":
        # A close before the accept reaches a browser as a dropped connection
        # (1006) with no code to read, so accept and then close.
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": CLOSE_CODE})
        return
    body = format_error_response(409, say("clientOutdated"), name="ClientOutdated")
    await JSONResponse(body, status_code=409)(scope, receive, send)
