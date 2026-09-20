"""Public forge webhook ingress with deployment-initiated WebSocket delivery.

Run one worker per relay. A disconnected or busy deployment gets HTTP 503.
Deployments reconcile periodically after lost events, including failed deliveries.
Only repository invalidations cross the socket, never webhook bodies or tokens.
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field

import jwt
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.forge_events import project_secret, verify_subscriptions

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
MAX_BODY = 5 * 1024 * 1024


@dataclass
class Connection:
    socket: WebSocket
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscriptions: dict[tuple[int, str], float] = field(default_factory=dict)


connections: dict[str, Connection] = {}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "forge-events"}


@app.websocket("/forge/events/{deployment}/connect")
async def connect(socket: WebSocket, deployment: str):
    secret = settings.forge_event_relay_keys.get(deployment)
    provided = socket.headers.get("authorization", "").removeprefix("Bearer ")
    if not secret or not hmac.compare_digest(provided, secret):
        await socket.close(code=1008)
        return
    # During a rolling release the new backend retries until the old one exits.
    if deployment in connections:
        await socket.close(code=1013)
        return
    await socket.accept()
    connection = Connection(socket)
    connections[deployment] = connection
    try:
        while True:
            raw = await socket.receive_text()
            if len(raw) > 65536:
                await socket.close(code=1009)
                return
            try:
                message = json.loads(raw)
                if (
                    not isinstance(message, dict)
                    or message.get("kind") != "github_subscriptions"
                ):
                    raise ValueError("Unknown subscription message")
                app_id = settings.forge_event_github_app_id
                public_key = settings.forge_event_github_public_key
                if not app_id or not public_key:
                    raise ValueError("Subscription verification is not configured")
                rows, expires = verify_subscriptions(
                    message["assertion"],
                    app_id=app_id,
                    public_key=public_key,
                    deployment=deployment,
                )
            except (ValueError, KeyError, TypeError, jwt.PyJWTError):
                await socket.close(code=1008)
                return
            connection.subscriptions = dict.fromkeys(rows, expires)
            async with connection.lock:
                await socket.send_json({"kind": "subscription_ack"})
    except WebSocketDisconnect:
        pass
    finally:
        if connections.get(deployment) is connection:
            connections.pop(deployment)


async def signed_payload(request: Request, secret: str, *, kind: str) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HTTPException(413)
    if kind == "forgejo":
        signature = request.headers.get("x-forgejo-signature", "")
    else:
        signature = request.headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            raise HTTPException(401)
        signature = signature.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401)
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400) from None
    if not isinstance(payload, dict):
        raise HTTPException(400)
    return payload


def repository_name(payload: dict) -> str | None:
    repository = payload.get("repository") or {}
    if not isinstance(repository, dict):
        raise HTTPException(400)
    repo = repository.get("full_name")
    if not repo:
        return None
    if not isinstance(repo, str) or len(repo) > 512 or repo.count("/") != 1:
        raise HTTPException(400)
    return repo


async def deliver(deployment: str, event: dict) -> bool:
    connection = connections.get(deployment)
    if connection is None:
        return False
    try:
        async with asyncio.timeout(5), connection.lock:
            await connection.socket.send_json(event)
    except (OSError, RuntimeError, TimeoutError):
        return False
    return True


@app.post("/forge/events/github-app", status_code=202)
async def receive_github_app(request: Request):
    secret = settings.forge_event_github_secret
    if not secret:
        raise HTTPException(404)
    payload = await signed_payload(request, secret, kind="github_app")
    repo = repository_name(payload)
    if repo is None:
        return {"accepted": True}
    installation = payload.get("installation")
    if not isinstance(installation, dict) or type(installation.get("id")) is not int:
        raise HTTPException(400)
    # Static routes are operator grants; dynamic routes require an App signature.
    deployments = set(
        settings.forge_event_github_installations.get(str(installation["id"]), [])
    )
    deployments.update(
        name
        for name, connection in connections.items()
        if connection.subscriptions.get((installation["id"], repo.lower()), 0)
        > time.time()
    )
    results = await asyncio.gather(
        *(
            deliver(name, {"kind": "github_app", "repo": repo})
            for name in set(deployments)
        )
    )
    if not all(results):
        raise HTTPException(503, "A deployment could not receive the event")
    return {"accepted": True}


@app.post("/forge/events/{deployment}", status_code=202)
@app.post("/forge/events/{deployment}/{project_id}", status_code=202)
async def receive(
    request: Request, deployment: str, project_id: uuid.UUID | None = None
):
    secret = settings.forge_event_relay_keys.get(deployment)
    if not secret:
        raise HTTPException(404)
    if project_id is not None:
        secret = project_secret(secret, project_id)
    kind = "forgejo" if "x-forgejo-signature" in request.headers else "github_app"
    payload = await signed_payload(request, secret, kind=kind)
    repo = repository_name(payload)
    if repo is None:
        return {"accepted": True}
    event = {"kind": kind, "repo": repo}
    if project_id is not None:
        event["project_id"] = str(project_id)
    if not await deliver(deployment, event):
        raise HTTPException(503, "Deployment could not receive the event")
    return {"accepted": True}
