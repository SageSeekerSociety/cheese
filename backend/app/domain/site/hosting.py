"""Serve private project websites without exposing platform routes or tokens."""

import asyncio
import hashlib
import hmac
import mimetypes
import re
import time
import uuid
from contextlib import aclosing
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import jwt
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AppError, BaseError, ValidationError
from app.domain.site.services import (
    get_current_release,
    read_release_file,
    require_site_access,
)

GRANT_TTL = 30
SESSION_TTL = 8 * 3600
AUTH_PATH = "/_cheese/session"


def content_origin(project_id: uuid.UUID) -> str:
    domain = settings.sites_domain.strip().lower()
    if not domain or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", domain):
        raise ValidationError("Site 托管域名尚未配置")
    local = domain == "localhost" or domain.endswith(".localhost")
    if settings.sites_scheme != "https" and not (
        local and settings.sites_scheme == "http"
    ):
        raise ValidationError("Site 托管需要 HTTPS")
    platform_host = urlsplit(settings.frontend_url).hostname or ""
    if not local and (
        len(domain.split(".")) < 2
        or domain.split(".")[-2:] == platform_host.split(".")[-2:]
    ):
        # Conservative for multi-label suffixes such as co.uk: deployments can
        # use distinct top-level domains instead of relying on a stale PSL.
        raise ValidationError("Site 托管域名必须与平台域名隔离")
    port = f":{settings.sites_port}" if settings.sites_port else ""
    return f"{settings.sites_scheme}://{project_id.hex}.{domain}{port}"


def _key() -> bytes:
    return hmac.digest(
        settings.jwt_secret.encode(), b"cheese:site-read:v1", hashlib.sha256
    )


def mint_site_token(
    project_id: uuid.UUID, handle: str, *, purpose: str, ttl: int
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": handle,
            "project": str(project_id),
            "aud": content_origin(project_id),
            "type": purpose,
            "iat": now,
            "exp": now + ttl,
        },
        _key(),
        algorithm="HS256",
    )


def _handle(token: str, project_id: uuid.UUID, purpose: str) -> str | None:
    try:
        claims = jwt.decode(
            token,
            _key(),
            algorithms=["HS256"],
            audience=content_origin(project_id),
            options={"require": ["sub", "project", "aud", "type", "iat", "exp"]},
        )
    except (jwt.PyJWTError, AppError, BaseError):
        return None
    if claims["project"] != str(project_id) or claims["type"] != purpose:
        return None
    return claims["sub"] if isinstance(claims["sub"], str) and claims["sub"] else None


def _cookie_name() -> str:
    return (
        "__Host-cheese-site"
        if settings.sites_scheme == "https"
        else "cheese-site-local"
    )


def _private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Keep localStorage and ordinary scripts, but prevent persistent interception
    # by a Service Worker after project membership is revoked.
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-scripts allow-same-origin allow-forms allow-downloads "
        "allow-popups allow-popups-to-escape-sandbox; "
        "worker-src 'none'; object-src 'none'; frame-ancestors 'self'"
    )
    return response


class SiteHostMiddleware:
    """Claim the whole content domain before any platform middleware or router."""

    def __init__(self, app: ASGIApp, platform: FastAPI) -> None:
        self.app = app
        self.platform = platform

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        host = (
            headers.get(b"host", b"")
            .decode("latin1")
            .split(":", 1)[0]
            .lower()
            .rstrip(".")
        )
        domain = settings.sites_domain.strip().lower()
        if not domain or not (host == domain or host.endswith("." + domain)):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        label = host.removesuffix("." + domain)
        if not re.fullmatch(r"[0-9a-f]{32}", label):
            await _private(Response(status_code=404))(scope, receive, send)
            return
        project_id = uuid.UUID(hex=label)
        request = Request(scope, receive)
        try:
            # Config validation also runs on direct requests, not just launches.
            content_origin(project_id)
            response = await self.respond(request, project_id)
        except (AppError, BaseError):
            response = Response("Site unavailable", status_code=404)
        await _private(response)(scope, receive, send)

    async def respond(self, request: Request, project_id: uuid.UUID) -> Response:
        is_exchange = request.url.path == AUTH_PATH
        destination = "/"
        if is_exchange:
            if request.method != "POST":
                return Response(status_code=405)
            origin = urlsplit(settings.frontend_url)
            if request.headers.get("origin") != f"{origin.scheme}://{origin.netloc}":
                return Response(status_code=403)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    return Response(status_code=413)
            values = parse_qs(body.decode("utf-8", errors="replace"))
            handle = _handle(values.get("grant", [""])[0], project_id, "site-grant")
            destination = values.get("path", ["/"])[0]
            if (
                not destination.startswith("/")
                or destination.startswith("//")
                or "\\" in destination
                or any(ord(char) < 32 for char in destination)
            ):
                return Response(status_code=400)
        else:
            if request.method not in ("GET", "HEAD"):
                return Response(status_code=405)
            handle = _handle(
                request.cookies.get(_cookie_name(), ""), project_id, "site-session"
            )
        if not handle:
            if (
                not is_exchange
                and request.method == "GET"
                and request.headers.get("sec-fetch-mode") == "navigate"
            ):
                path = request.url.path + (
                    "?" + request.url.query if request.url.query else ""
                )
                return RedirectResponse(
                    f"{settings.frontend_url.rstrip('/')}/sites/{project_id}"
                    f"?{urlencode({'path': path})}",
                    status_code=303,
                )
            return Response("Sign in through Cheese to open this Site", status_code=401)
        provider = self.platform.dependency_overrides.get(get_db, get_db)
        async with aclosing(provider()) as sessions:
            session = await anext(sessions)
            await require_site_access(session, handle, project_id)
            release = await get_current_release(session, project_id)
            if release is None:
                return Response("Site unavailable", status_code=404)
            if is_exchange:
                response = RedirectResponse(destination, status_code=303)
                response.set_cookie(
                    _cookie_name(),
                    mint_site_token(
                        project_id, handle, purpose="site-session", ttl=SESSION_TTL
                    ),
                    max_age=SESSION_TTL,
                    path="/",
                    httponly=True,
                    secure=settings.sites_scheme == "https",
                    samesite="lax",
                )
                return response
            if request.headers.get("service-worker") == "script":
                return Response(status_code=403)
            path = request.url.path.lstrip("/")
            if not path or path.endswith("/"):
                path += "index.html"
            body = await asyncio.to_thread(read_release_file, release, path)
            if body is None:
                if (
                    not path.endswith("/")
                    and await asyncio.to_thread(read_release_file, release, path + "/")
                    is not None
                ):
                    suffix = "?" + request.url.query if request.url.query else ""
                    return RedirectResponse(
                        "/" + quote(path, safe="/") + "/" + suffix, status_code=307
                    )
                return Response("File not found", status_code=404)
            media = mimetypes.guess_type(path)[0] or "application/octet-stream"
            response = Response(
                body if request.method != "HEAD" else b"", media_type=media
            )
            response.headers["Content-Length"] = str(len(body))
            return response
