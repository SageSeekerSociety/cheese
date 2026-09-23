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
from app.domain.library import service as library
from app.domain.room_task.place import Place
from app.domain.site.hosting import content_origin
from app.domain.topic.services import TopicService

AUTH_PATH = "/_cheese/session"
# 房间文件在这个内容域上的地址。它和 `AUTH_PATH` 同住 `/_cheese/` 这个命名空间：
# 那一段是留给平台的，artifact 自己的文件不会叫这个名字。
ROOM_FILES_PATH = "/_cheese/room/"
GRANT_TTL = 30
SESSION_TTL = 8 * 3600
APP_MIME = "application/x-cheesex-app"


def preview_origin(topic_id: uuid.UUID) -> str:
    # Sites already validates the dedicated content domain and its TLS settings.
    return content_origin(topic_id).replace("://", "://preview-", 1)


def room_file_path(path: str) -> str | None:
    """这个内容域上的路径指的是哪一份房间文件；不是就 None。

    房间文件不挂在当前 artifact 下面——一份人传上来的页面、一份芝士写下但还没摆出
    来的报告，都在这个树的别处。所以它有自己的地址，按房间相对路径寻址。
    """
    if not path.startswith(ROOM_FILES_PATH):
        return None
    return path[len(ROOM_FILES_PATH) :]


def room_file_addressable(relative: str) -> bool:
    """这个房间相对路径能不能被取。

    房间树由人和 agent 写，磁盘上一个带反斜杠、以 `.` 开头的名字都是合法的，但在这
    个地址空间里没有它的位置——artifact 那条路拒绝的形状和这里一致（见
    :func:`app.domain.library.service.read_preview_file`）。真正的包含关系由
    `read_room_file` 的 `_safe_path` 判，这里只管形状。
    """
    parts = relative.split("/")
    return bool(relative) and all(
        part and not part.startswith(".") and "\\" not in part and "\x00" not in part
        for part in parts
    )


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
    actor = Actor(handle=handle, user_id=None, via="token")
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
        # 一份房间文件有自己的地址（`ROOM_FILES_PATH`）。它不挂在当前 artifact 下
        # 面，也不需要房间先摆出过东西——人传上来的页面、芝士写完还没摆的报告，都
        # 是房间文件。所以只有 artifact 那条路才去要 artifact。
        target = room_file_path(destination if exchange else request.url.path)
        if target is not None and not room_file_addressable(target):
            return Response("Preview unavailable", status_code=404)
        async with self.sessions() as sessions:
            session = await anext(sessions)
            place = await require_preview_access(session, topic_id, claims["sub"])
            artifact = None
            if target is None:
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
            project = place.project_id
        if target is not None:
            if request.method not in {"GET", "HEAD"}:
                return Response(status_code=405)
            data = await asyncio.to_thread(
                library.read_room_file, project, topic_id, target
            )
            media = mimetypes.guess_type(target)[0]
            response = Response(
                data if request.method != "HEAD" else b"",
                media_type=media or "application/octet-stream",
            )
            response.headers["Content-Length"] = str(len(data))
            return response
        assert artifact is not None  # 上面那条已经为 None 的情形返回了。
        mime, entry = artifact.mime_type, artifact.content
        if mime == APP_MIME:
            return await relay_http(topic_id, request)
        if request.method not in {"GET", "HEAD"}:
            return Response(status_code=405)
        relative = request.url.path.lstrip("/") or PurePosixPath(entry).name
        data = await asyncio.to_thread(
            library.read_preview_file, project, topic_id, entry, relative
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
