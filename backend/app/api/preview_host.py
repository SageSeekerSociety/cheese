"""Topic previews have their own origin and never carry platform credentials."""

import asyncio
import hashlib
import hmac
import json
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
from starlette.requests import ClientDisconnect, Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketState

from app.api.auth import ActorResolver
from app.api.preview_runtime import inject_runtime_script
from app.api.routes.app_preview import relay_http, relay_ws
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AppError, BaseError, NotFoundError
from app.domain.block.repositories import BlockRepository
from app.domain.identity.actor import Actor
from app.domain.library import service as library
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.site.hosting import DEVICE_FEATURES_OFF, content_origin

AUTH_PATH = "/_cheese/session"
# 房间文件在这个内容域上的地址。它和 `AUTH_PATH` 同住 `/_cheese/` 这个命名空间：
# 那一段是留给平台的，artifact 自己的文件不会叫这个名字。
ROOM_FILES_PATH = "/_cheese/room/"
GRANT_TTL = 30
SESSION_TTL = 8 * 3600
APP_MIME = "application/x-cheesex-app"


def resource_key(resource: dict) -> str:
    return hashlib.sha256(
        json.dumps(resource, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:22]


def preview_origin(topic_id: uuid.UUID, resource: dict | None = None) -> str:
    # One DNS label stays covered by the content domain's existing wildcard TLS.
    origin = content_origin(topic_id).replace("://", "://preview-", 1)
    if resource:
        origin = origin.replace(
            topic_id.hex, topic_id.hex + "-" + resource_key(resource), 1
        )
    return origin


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
    topic_id: uuid.UUID,
    handle: str,
    *,
    purpose: str,
    ttl: int,
    resource: dict | None = None,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": handle,
            "topic": str(topic_id),
            "aud": preview_origin(topic_id, resource),
            **({"resource": resource} if resource else {}),
            "type": purpose,
            "iat": now,
            "exp": now + ttl,
        },
        _key(),
        algorithm="HS256",
    )


def _claims(
    token: str, topic_id: uuid.UUID, purpose: str, origin: str | None = None
) -> dict | None:
    try:
        claims = jwt.decode(
            token,
            _key(),
            algorithms=["HS256"],
            audience=origin or preview_origin(topic_id),
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
    if preview_origin(topic_id, claims.get("resource")) != (
        origin or preview_origin(topic_id)
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
    """The conversation a preview belongs to — a room's, or one of its tasks' —
    once ``handle`` may see its room."""
    place = await PlaceResolver(session).conversation(topic_id)
    if place is None:
        raise NotFoundError("Preview unavailable")
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
    response.headers["Permissions-Policy"] = DEVICE_FEATURES_OFF
    # A link the page opens in a new tab is the person leaving the preview: the
    # document's own sandbox has to allow it as well as the iframe's (each can only
    # take flags away), and the site it opens is not ours to sandbox. Same as sites.
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-scripts allow-same-origin allow-forms allow-downloads "
        "allow-popups allow-popups-to-escape-sandbox; "
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


HTML_MEDIA = {"text/html", "application/xhtml+xml"}


def _served_body(data: bytes, media: str | None) -> bytes:
    """这一段字节发出去时的样子：HTML 文档装上运行时，其余原样。

    只动真 HTML（product decision：普通报告里也要有键盘桥）。静态 artifact 的
    409 版本校验在那之前已经拿**原始**字节算过了，注入发生在它后面。
    """
    return inject_runtime_script(data) if media in HTML_MEDIA else data


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
            re.fullmatch(
                r"preview-([0-9a-f]{32})(?:-([0-9a-f]{22}))?\." + re.escape(domain),
                host,
            )
            if domain
            else None
        )
        if not match:
            await self.app(scope, receive, send)
            return
        # Misrouted owner-mode traffic must not create a second local owner.
        if settings.preview_connection_mode == "owner" and not hasattr(
            self.platform.state, "preview_hub"
        ):
            if scope["type"] == "websocket":
                await WebSocket(scope, receive, send).close(code=1013)
            else:
                await Response(
                    status_code=503,
                    headers={"Retry-After": "1", "Cache-Control": "no-store"},
                )(scope, receive, send)
            return
        topic_id = uuid.UUID(hex=match[1])
        origin = preview_origin(topic_id)
        if match[2]:
            origin = origin.replace(topic_id.hex, topic_id.hex + "-" + match[2], 1)
        if scope["type"] == "websocket":
            await self.websocket(WebSocket(scope, receive, send), topic_id, origin)
            return
        request = Request(scope, receive)
        try:
            preview_origin(topic_id)
            response = await self.respond(request, topic_id, origin)
        except (AppError, BaseError):
            response = Response("Preview unavailable", status_code=404)
        except ClientDisconnect:
            # The browser dropped the request before we read it: a dev server's
            # page reloading cancels its in-flight module fetches. Nobody is
            # left to answer, and nothing here failed. This middleware sits
            # outside the platform's exception handlers, so without this the
            # hang-up reaches the catch-all and pages as a server error.
            return
        await _private(response)(scope, receive, send)

    def sessions(self):
        provider = self.platform.dependency_overrides.get(get_db, get_db)
        return aclosing(provider())

    async def respond(
        self, request: Request, topic_id: uuid.UUID, content: str | None = None
    ) -> Response:
        content = content or preview_origin(topic_id)
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
            claims = _claims(
                values.get("grant", [""])[0], topic_id, "preview-grant", content
            )
            destination = values.get("path", ["/"])[0]
            if not _destination(destination):
                return Response(status_code=400)
        else:
            # Sibling previews share a top-level storage partition. Block their
            # fetches, including no-cors GETs that carry no Origin header.
            origin = request.headers.get("origin")
            if origin and origin != content:
                return Response(status_code=403)
            if (
                request.headers.get("sec-fetch-site") in {"same-site", "cross-site"}
                and request.headers.get("sec-fetch-mode") != "navigate"
            ):
                return Response(status_code=403)
            claims = _claims(
                request.cookies.get(cookie_name(), ""),
                topic_id,
                "preview-session",
                content,
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
        resource = claims.get("resource")
        if resource:
            # A fixed grant never resolves latest again, including subresources.
            if resource["kind"] == "file" and exchange:
                destination = "/"
            target = None
        else:
            target = room_file_path(destination if exchange else request.url.path)
        if target is not None and not room_file_addressable(target):
            return Response("Preview unavailable", status_code=404)
        async with self.sessions() as sessions:
            session = await anext(sessions)
            place = await require_preview_access(session, topic_id, claims["sub"])
            if request.url.path == "/_cheese/runtime.js" and not exchange:
                from app.api.preview_runtime import RUNTIME_SCRIPT

                platform = urlsplit(settings.frontend_url)
                script = RUNTIME_SCRIPT.replace(
                    "__PLATFORM_ORIGIN__",
                    json.dumps(f"{platform.scheme}://{platform.netloc}"),
                )
                if request.method not in {"GET", "HEAD"}:
                    return Response(status_code=405)
                response = Response(
                    script if request.method == "GET" else b"",
                    media_type="text/javascript",
                )
                response.headers["Content-Length"] = str(len(script.encode()))
                return response
            artifact = None
            if target is None and resource is None:
                artifact = await BlockRepository(session).latest_artifact(
                    place.conversation_id
                )
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
                        resource=claims.get("resource"),
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
            # A task's files are its room's.
            project, room = place.project_id, place.room_id
        if target is not None:
            if request.method not in {"GET", "HEAD"}:
                return Response(status_code=405)
            data = await asyncio.to_thread(
                library.read_room_file, project, room, target
            )
            media = mimetypes.guess_type(target)[0]
            body = _served_body(data, media)
            response = Response(
                body if request.method != "HEAD" else b"",
                media_type=media or "application/octet-stream",
            )
            response.headers["Content-Length"] = str(len(body))
            return response
        if resource:
            if resource["kind"] == "app":
                return await relay_http(
                    topic_id, resource["seat"], request, instance=resource["instance"]
                )
            mime, entry = resource["mime"], resource["path"]
        else:
            assert artifact is not None
            mime, entry = artifact.mime_type, artifact.content
            if mime == APP_MIME:
                return await relay_http(topic_id, artifact.author, request)
        if request.method not in {"GET", "HEAD"}:
            return Response(status_code=405)
        relative = request.url.path.lstrip("/") or PurePosixPath(entry).name
        data = await asyncio.to_thread(
            library.read_preview_file, project, room, entry, relative
        )
        if (
            resource
            and relative == PurePosixPath(entry).name
            and hashlib.sha256(data).hexdigest()[:16] != resource["version"]
        ):
            return Response(
                "Preview entry changed; open the current version", status_code=409
            )
        media = (
            mime
            if relative == PurePosixPath(entry).name
            else mimetypes.guess_type(relative)[0]
        )
        body = _served_body(data, media)
        response = Response(
            body if request.method != "HEAD" else b"",
            media_type=media or "application/octet-stream",
        )
        response.headers["Content-Length"] = str(len(body))
        return response

    async def websocket(
        self, websocket: WebSocket, topic_id: uuid.UUID, content: str | None = None
    ) -> None:
        content = content or preview_origin(topic_id)
        claims = _claims(
            websocket.cookies.get(cookie_name(), ""),
            topic_id,
            "preview-session",
            content,
        )
        if not claims or websocket.headers.get("origin") != content:
            await websocket.close(code=1008)
            return
        try:
            async with self.sessions() as sessions:
                session = await anext(sessions)
                place = await require_preview_access(session, topic_id, claims["sub"])
                resource = claims.get("resource")
                if resource:
                    if resource["kind"] != "app":
                        raise NotFoundError("Preview unavailable")
                    seat = resource["seat"]
                else:
                    artifact = await BlockRepository(session).latest_artifact(
                        place.conversation_id
                    )
                    if artifact is None or artifact.mime_type != APP_MIME:
                        raise NotFoundError("Preview unavailable")
                    seat = artifact.author
            # No DB session or read transaction lives for the HMR connection.
            async with asyncio.timeout(max(0, claims["exp"] - time.time())):
                await relay_ws(
                    websocket,
                    topic_id,
                    seat,
                    **({"instance": resource["instance"]} if resource else {}),
                )
        except (AppError, BaseError, TimeoutError):
            if websocket.application_state != WebSocketState.DISCONNECTED:
                await websocket.close(code=1008)
