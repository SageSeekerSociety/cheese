"""The mini-app shell, on the content domain: ``apps.<SITES_DOMAIN>``.

A document's mini-app runs in a frame of this host, so it runs in a process
of its own and under rules the browser enforces from these headers, which the
code inside cannot lift. The host serves one thing — the shell the frontend
builds (`frontend/miniapp/`), by major version, at ``/v<n>/`` — and the shell
holds nothing private: the code arrives from the document's page, which read it
with the reader's own permission (``app.api.routes.doc_apps``). So there is no
session here, no cookie and no check of who asks.

The frame gets ``sandbox="allow-scripts"`` and no ``allow-same-origin``, and
the policy below says the same: every app is an origin of its own that nothing
else shares — no cookies, no storage, no other app's page. It reaches nothing
outside itself: no request leaves it, no form is sent, and it may only be
framed by the platform.
"""

import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.errors import AppError, BaseError
from app.domain.site.hosting import DEVICE_FEATURES_OFF, content_host

#: The label of the shell's host on the content domain. Not ``preview-…``: the
#: edge sends those to the preview connection's own service.
LABEL = "apps"
_PATH = re.compile(r"^/v([1-9][0-9]?)/(?:index\.html)?$")
#: How long a fetched shell is served before it is fetched again: a release
#: replaces it in place (only compatible changes stay within a major).
_KEEP_S = 300.0
#: The shells fetched so far, by major, for the life of this process: a few
#: megabytes at most, one entry per major ever served.
_kept: dict[int, tuple[float, bytes]] = {}


def shell_origin() -> str | None:
    """Where the shell is served, or None when this deployment has no content
    domain (then no mini-app runs)."""
    if not settings.miniapp_shell_url:
        return None
    try:
        return content_host(LABEL)
    except (AppError, BaseError):
        return None


def shell_url(shell: int) -> str | None:
    origin = shell_origin()
    return f"{origin}/v{shell}/" if origin else None


def _policy() -> str:
    parts = urlsplit(settings.frontend_url)
    platform = f"{parts.scheme}://{parts.netloc}"
    return (
        "sandbox allow-scripts; default-src 'none'; "
        "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data: blob:; font-src data:; media-src data: blob:; "
        "connect-src 'none'; form-action 'none'; base-uri 'none'; "
        f"frame-ancestors {platform}"
    )


async def _shell(major: int) -> bytes | None:
    now = time.monotonic()
    kept = _kept.get(major)
    if kept and now - kept[0] < _KEEP_S:
        return kept[1]
    base = (settings.miniapp_shell_url or "").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/v{major}/index.html")
    except httpx.HTTPError:
        return kept[1] if kept else None
    if r.status_code != 200:
        return None
    _kept[major] = (now, r.content)
    return r.content


class AppShellHostMiddleware:
    """Answer the shell's host before any platform route can."""

    def __init__(self, app: ASGIApp, platform: FastAPI) -> None:
        self.app = app
        self.platform = platform

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        origin = shell_origin()
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin1").lower().rstrip(".")
        if origin is None or host != urlsplit(origin).netloc:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        match = _PATH.match(scope.get("path", ""))
        body = None
        if match and scope.get("method") in ("GET", "HEAD"):
            body = await _shell(int(match.group(1)))
        if body is None:
            response = Response(status_code=404)
        else:
            response = Response(
                body if scope.get("method") == "GET" else b"",
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": f"public, max-age={int(_KEEP_S)}"},
            )
        response.headers["Content-Security-Policy"] = _policy()
        response.headers["Permissions-Policy"] = DEVICE_FEATURES_OFF
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        await response(scope, receive, send)
