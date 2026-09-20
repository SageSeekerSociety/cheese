"""Topic previews have their own origin and never carry platform credentials."""

import asyncio
import hashlib
import hmac
import mimetypes
import re
import time
import uuid
from contextlib import aclosing
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketState

from app.api.auth import ActorResolver
from app.api.routes.app_preview import relay_http, relay_ws
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AppError, BaseError, NotFoundError
from app.domain.block.repositories import BlockRepository
from app.domain.identity.actor import Actor
from app.domain.room_task.place import Place
from app.domain.site.hosting import content_origin
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws

AUTH_PATH = "/_cheese/session"
GRANT_TTL = 30
SESSION_TTL = 8 * 3600
APP_MIME = "application/x-cheesex-app"


def preview_origin(topic_id: uuid.UUID) -> str:
    # Sites already validates the dedicated content domain and its TLS settings.
    return content_origin(topic_id).replace("://", "://preview-", 1)


def _key() -> bytes:
    return hmac.digest(
        settings.jwt_secret.encode(), b"cheese:topic-preview:v1", hashlib.sha256
    )


def mint_preview_token(
    topic_id: uuid.UUID, handle: str, *, purpose: str, ttl: int
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": handle,
            "topic": str(topic_id),
            "aud": preview_origin(topic_id),
            "type": purpose,
            "iat": now,
            "exp": now + ttl,
        },
        _key(),
        algorithm="HS256",
    )


def _claims(token: str, topic_id: uuid.UUID, purpose: str) -> dict | None:
    try:
        claims = jwt.decode(
            token,
            _key(),
            algorithms=["HS256"],
            audience=preview_origin(topic_id),
            options={"require": ["sub", "topic", "aud", "type", "iat", "exp"]},
        )
    except (jwt.PyJWTError, AppError, BaseError):
        return None
    if (
        claims["topic"] != str(topic_id)
        or claims["type"] != purpose
        or not isinstance(claims["sub"], str)
        or not claims["sub"]
    ):
        return None
    return claims


def cookie_name() -> str:
    return (
        "__Host-cheese-preview"
        if settings.sites_scheme == "https"
        else "cheese-preview-local"
    )


async def require_preview_access(
    session: AsyncSession, topic_id: uuid.UUID, handle: str
) -> Place:
    place = await TopicService(session).place_or_404(topic_id)
    resolver = ActorResolver(session=session, bearer=None, cheese_token="")
    actor = Actor(handle=handle, user_id=None, is_agent=False, via="token")
    if not await resolver.can_access_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    ):
        raise NotFoundError("Preview unavailable")
    return place


def _private(response: Response) -> Response:
    platform = urlsplit(settings.frontend_url)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-scripts allow-same-origin allow-forms allow-downloads; "
        "worker-src 'none'; object-src 'none'; "
        f"frame-ancestors 'self' {platform.scheme}://{platform.netloc}"
    )
    return response


def _destination(path: str) -> bool:
    return (
        path.startswith("/")
        and not path.startswith("//")
        and "\\" not in path
        and not any(ord(char) < 32 for char in path)
    )


class PreviewHostMiddleware:
    """Intercept preview hosts outside both platform and published-Site routing."""

    def __init__(self, app: ASGIApp, platform: FastAPI) -> None:
        self.app, self.platform = app, platform

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        host = (
            dict(scope.get("headers", []))
            .get(b"host", b"")
            .decode("latin1")
            .split(":", 1)[0]
            .lower()
            .rstrip(".")
        )
        domain = settings.sites_domain.strip().lower()
        match = (
            re.fullmatch(r"preview-([0-9a-f]{32})\." + re.escape(domain), host)
            if domain
            else None
        )
        if not match:
            await self.app(scope, receive, send)
            return
        topic_id = uuid.UUID(hex=match[1])
        if scope["type"] == "websocket":
            await self.websocket(WebSocket(scope, receive, send), topic_id)
            return
        request = Request(scope, receive)
        try:
            preview_origin(topic_id)
            response = await self.respond(request, topic_id)
        except (AppError, BaseError):
            response = Response("Preview unavailable", status_code=404)
        await _private(response)(scope, receive, send)

    def sessions(self):
        provider = self.platform.dependency_overrides.get(get_db, get_db)
        return aclosing(provider())

    async def respond(self, request: Request, topic_id: uuid.UUID) -> Response:
        exchange = request.url.path == AUTH_PATH
        destination = "/"
        if exchange:
            if request.method != "POST":
                return Response(status_code=405)
            platform = urlsplit(settings.frontend_url)
            if (
                request.headers.get("origin")
                != f"{platform.scheme}://{platform.netloc}"
            ):
                return Response(status_code=403)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    return Response(status_code=413)
            values = parse_qs(body.decode("utf-8", errors="replace"))
            claims = _claims(values.get("grant", [""])[0], topic_id, "preview-grant")
            destination = values.get("path", ["/"])[0]
            if not _destination(destination):
                return Response(status_code=400)
        else:
            # Sibling previews share a top-level storage partition. Block their
            # fetches, including no-cors GETs that carry no Origin header.
            origin = request.headers.get("origin")
            if origin and origin != preview_origin(topic_id):
                return Response(status_code=403)
            if (
                request.headers.get("sec-fetch-site") in {"same-site", "cross-site"}
                and request.headers.get("sec-fetch-mode") != "navigate"
            ):
                return Response(status_code=403)
            claims = _claims(
                request.cookies.get(cookie_name(), ""), topic_id, "preview-session"
            )
        if not claims:
            if (
                not exchange
                and request.method == "GET"
                and request.headers.get("sec-fetch-mode") == "navigate"
            ):
                path = request.url.path + (
                    "?" + request.url.query if request.url.query else ""
                )
                return RedirectResponse(
                    f"{settings.frontend_url.rstrip('/')}/previews/{topic_id}"
                    f"?{urlencode({'path': path})}",
                    status_code=303,
                )
            return Response("Open this preview through Cheese", status_code=401)
        if request.headers.get("service-worker") == "script":
            return Response(status_code=403)
        async with self.sessions() as sessions:
            session = await anext(sessions)
            place = await require_preview_access(session, topic_id, claims["sub"])
            artifact = await BlockRepository(session).latest_artifact(place.room_id)
            if artifact is None:
                return Response("Preview unavailable", status_code=404)
            if exchange:
                response = RedirectResponse(destination, status_code=303)
                response.set_cookie(
                    cookie_name(),
                    mint_preview_token(
                        topic_id,
                        claims["sub"],
                        purpose="preview-session",
                        ttl=SESSION_TTL,
                    ),
                    max_age=SESSION_TTL,
                    path="/",
                    httponly=True,
                    secure=settings.sites_scheme == "https",
                    samesite="none" if settings.sites_scheme == "https" else "lax",
                )
                if settings.sites_scheme == "https":
                    # Python 3.11's cookie API lacks Partitioned. CHIPS keeps the
                    # embedded session usable when third-party cookies are blocked.
                    response.headers["set-cookie"] += "; Partitioned"
                return response
            mime, entry, project = (
                artifact.mime_type,
                artifact.content,
                place.project_id,
            )
        if mime == APP_MIME:
            return await relay_http(topic_id, request)
        if request.method not in {"GET", "HEAD"}:
            return Response(status_code=405)
        relative = request.url.path.lstrip("/") or PurePosixPath(entry).name
        data = await asyncio.to_thread(
            ws.read_preview_file, project, topic_id, entry, relative
        )
        media = (
            mime
            if relative == PurePosixPath(entry).name
            else mimetypes.guess_type(relative)[0]
        )
        response = Response(
            data if request.method != "HEAD" else b"",
            media_type=media or "application/octet-stream",
        )
        response.headers["Content-Length"] = str(len(data))
        return response

    async def websocket(self, websocket: WebSocket, topic_id: uuid.UUID) -> None:
        claims = _claims(
            websocket.cookies.get(cookie_name(), ""), topic_id, "preview-session"
        )
        if not claims or websocket.headers.get("origin") != preview_origin(topic_id):
            await websocket.close(code=1008)
            return
        try:
            async with self.sessions() as sessions:
                session = await anext(sessions)
                place = await require_preview_access(session, topic_id, claims["sub"])
                artifact = await BlockRepository(session).latest_artifact(place.room_id)
                if artifact is None or artifact.mime_type != APP_MIME:
                    raise NotFoundError("Preview unavailable")
            # No DB session or read transaction lives for the HMR connection.
            async with asyncio.timeout(max(0, claims["exp"] - time.time())):
                await relay_ws(websocket, topic_id)
        except (AppError, BaseError, TimeoutError):
            if websocket.application_state != WebSocketState.DISCONNECTED:
                await websocket.close(code=1008)
