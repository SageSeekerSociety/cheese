"""Public forge webhook ingress with deployment-initiated WebSocket delivery.

Run one worker per relay. A disconnected or busy deployment gets HTTP 503 so
the forge can retry; deployments also reconcile periodically after lost events.
Only repository invalidations cross the socket, never webhook bodies or tokens.
"""

import asyncio
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.forge_events import project_secret

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
MAX_BODY = 5 * 1024 * 1024


@dataclass
class Connection:
    socket: WebSocket
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if connections.get(deployment) is connection:
            connections.pop(deployment)


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
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HTTPException(413)
    if "x-forgejo-signature" in request.headers:
        kind = "forgejo"
        signature = request.headers["x-forgejo-signature"]
    else:
        kind = "github_app"
        signature = request.headers.get("x-hub-signature-256", "")
        if not signature.startswith("sha256="):
            raise HTTPException(401)
        signature = signature.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401)
    try:
        payload = json.loads(body)
        repository = payload.get("repository") or {}
        repo = repository.get("full_name")
    except (ValueError, AttributeError):
        raise HTTPException(400) from None
    if not repo:
        return {"accepted": True}  # App pings and installation events have no repo.
    if not isinstance(repo, str) or len(repo) > 512 or repo.count("/") != 1:
        raise HTTPException(400)
    connection = connections.get(deployment)
    if connection is None:
        raise HTTPException(503, "Deployment is disconnected")
    try:
        async with asyncio.timeout(5), connection.lock:
            event = {"kind": kind, "repo": repo}
            if project_id is not None:
                event["project_id"] = str(project_id)
            await connection.socket.send_json(event)
    except (OSError, RuntimeError, TimeoutError):
        raise HTTPException(503, "Deployment could not receive the event") from None
    return {"accepted": True}
