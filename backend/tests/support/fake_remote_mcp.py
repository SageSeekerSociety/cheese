"""A remote MCP server with its own OAuth authorization server, on a real port.

Stands in for a hosted MCP service (Linear, Notion, …) the way the platform
meets one: over HTTP, from outside the process. It implements the parts of the
specifications the platform relies on — protected resource and authorization
server metadata, dynamic client registration, authorization code with PKCE
S256 and `resource`, refresh with rotation, revocation — and records what it
was sent, so a test can say what reached the upstream and what did not.

Three MCP endpoints:
- `/mcp`: Streamable HTTP, OAuth-protected; answers tool calls as SSE.
- `/keyed`: Streamable HTTP, authorized by an `X-Api-Key` header; answers JSON.
- `/sse` + `/messages`: the 2024-11-05 HTTP+SSE transport, OAuth-protected.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

API_KEY = "key-for-the-keyed-server"
TOOL = {
    "name": "whoami",
    "description": "Say which credential reached the server.",
    "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}},
}


@dataclass
class Grant:
    client_id: str
    challenge: str
    redirect_uri: str
    resource: str


@dataclass
class Fake:
    base: str = ""
    clients: dict[str, dict] = field(default_factory=dict)
    codes: dict[str, Grant] = field(default_factory=dict)
    #: access token -> resource it was issued for
    access: dict[str, str] = field(default_factory=dict)
    #: refresh token -> (client_id, resource)
    refresh: dict[str, tuple[str, str]] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    token_requests: list[dict] = field(default_factory=list)
    authorize_requests: list[dict] = field(default_factory=list)
    #: every Authorization / X-Api-Key header an MCP endpoint received
    seen_credentials: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    refuse_refresh: bool = False
    refuse_revocation: bool = False
    sse_queues: dict[str, asyncio.Queue] = field(default_factory=dict)

    @property
    def issued(self) -> list[str]:
        return list(self.access)

    def expire_access_tokens(self) -> None:
        self.access.clear()

    # --- OAuth ------------------------------------------------------------------

    async def protected_resource(self, request: Request) -> Response:
        return JSONResponse(
            {
                "resource": f"{self.base}/mcp",
                "authorization_servers": [self.base],
                "scopes_supported": ["read"],
            }
        )

    async def authorization_server(self, request: Request) -> Response:
        return JSONResponse(
            {
                "issuer": self.base,
                "authorization_endpoint": f"{self.base}/authorize",
                "token_endpoint": f"{self.base}/token",
                "registration_endpoint": f"{self.base}/register",
                "revocation_endpoint": f"{self.base}/revoke",
                "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
            }
        )

    async def register(self, request: Request) -> Response:
        body = await request.json()
        client_id = "client-" + secrets.token_hex(4)
        self.clients[client_id] = body
        return JSONResponse(
            {"client_id": client_id, **body, "token_endpoint_auth_method": "none"},
            status_code=201,
        )

    async def authorize(self, request: Request) -> Response:
        query = dict(request.query_params)
        self.authorize_requests.append(query)
        client = self.clients.get(query.get("client_id", ""))
        if (
            client is None
            or query.get("redirect_uri") not in client["redirect_uris"]
            or query.get("code_challenge_method") != "S256"
            or not query.get("resource")
        ):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        code = secrets.token_urlsafe(12)
        self.codes[code] = Grant(
            client_id=query["client_id"],
            challenge=query["code_challenge"],
            redirect_uri=query["redirect_uri"],
            resource=query["resource"],
        )
        back = {"code": code, "state": query["state"], "iss": self.base}
        return RedirectResponse(f"{query['redirect_uri']}?{urlencode(back)}", 302)

    def _tokens(self, client_id: str, resource: str) -> dict:
        access = "at-" + secrets.token_hex(8)
        refresh = "rt-" + secrets.token_hex(8)
        self.access[access] = resource
        self.refresh[refresh] = (client_id, resource)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read",
        }

    async def token(self, request: Request) -> Response:
        form = {k: str(v) for k, v in (await request.form()).items()}
        self.token_requests.append(form)
        if form.get("grant_type") == "authorization_code":
            grant = self.codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "")
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            if (
                grant is None
                or grant.challenge != challenge
                or grant.client_id != form.get("client_id")
                or grant.redirect_uri != form.get("redirect_uri")
                or grant.resource != form.get("resource")
            ):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(self._tokens(grant.client_id, grant.resource))
        if form.get("grant_type") == "refresh_token":
            held = self.refresh.pop(form.get("refresh_token", ""), None)
            if (
                self.refuse_refresh
                or held is None
                or held[0] != form.get("client_id")
                or held[1] != form.get("resource")
            ):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(self._tokens(*held))
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    async def revoke(self, request: Request) -> Response:
        form = {k: str(v) for k, v in (await request.form()).items()}
        token = form.get("token", "")
        if self.refuse_revocation:
            return JSONResponse({"error": "unauthorized_client"}, status_code=400)
        self.revoked.append(token)
        self.access.pop(token, None)
        self.refresh.pop(token, None)
        return Response(status_code=200)

    # --- MCP --------------------------------------------------------------------

    def _challenge(self) -> Response:
        return Response(
            status_code=401,
            headers={
                "WWW-Authenticate": 'Bearer resource_metadata="'
                f'{self.base}/.well-known/oauth-protected-resource/mcp", scope="read"'
            },
        )

    def _bearer(self, request: Request) -> str | None:
        header = request.headers.get("authorization", "")
        if header:
            self.seen_credentials.append(header)
        token = header.removeprefix("Bearer ")
        return token if token in self.access else None

    def _answer(self, message: dict, credential: str) -> dict | None:
        method = message.get("method")
        if "id" not in message:
            return None
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            self.tool_calls.append(message["params"])
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": f"reached with {credential}; note="
                        + str(message["params"].get("arguments", {}).get("note")),
                    }
                ]
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": "Method not found"},
            }
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}

    async def mcp(self, request: Request) -> Response:
        token = self._bearer(request)
        if token is None:
            return self._challenge()
        message = await request.json()
        answer = self._answer(message, "oauth")
        if answer is None:
            return Response(status_code=202)
        headers = {}
        if message.get("method") == "initialize":
            headers["Mcp-Session-Id"] = secrets.token_hex(8)

        async def stream():
            yield b": keep-alive\n\n"
            yield b"event: message\ndata: " + json.dumps(answer).encode() + b"\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers=headers
        )

    async def keyed(self, request: Request) -> Response:
        key = request.headers.get("x-api-key", "")
        self.seen_credentials.append(f"X-Api-Key {key}")
        if key != API_KEY:
            return Response(status_code=401)
        message = await request.json()
        answer = self._answer(message, "api key")
        return JSONResponse(answer) if answer else Response(status_code=202)

    async def sse(self, request: Request) -> Response:
        if self._bearer(request) is None:
            return self._challenge()
        session = secrets.token_hex(6)
        queue: asyncio.Queue = asyncio.Queue()
        self.sse_queues[session] = queue

        async def stream():
            yield f"event: endpoint\ndata: /messages?session={session}\n\n".encode()
            while True:
                answer = await queue.get()
                yield b"event: message\ndata: " + json.dumps(answer).encode() + b"\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def messages(self, request: Request) -> Response:
        if self._bearer(request) is None:
            return self._challenge()
        queue = self.sse_queues.get(request.query_params.get("session", ""))
        if queue is None:
            return Response(status_code=404)
        answer = self._answer(await request.json(), "oauth over sse")
        if answer is not None:
            await queue.put(answer)
        return Response(status_code=202)

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route(
                    "/.well-known/oauth-protected-resource/mcp",
                    self.protected_resource,
                ),
                Route(
                    "/.well-known/oauth-authorization-server",
                    self.authorization_server,
                ),
                Route("/register", self.register, methods=["POST"]),
                Route("/authorize", self.authorize),
                Route("/token", self.token, methods=["POST"]),
                Route("/revoke", self.revoke, methods=["POST"]),
                Route("/mcp", self.mcp, methods=["POST"]),
                Route("/keyed", self.keyed, methods=["POST"]),
                Route("/sse", self.sse),
                Route("/messages", self.messages, methods=["POST"]),
            ]
        )


def serve() -> tuple[Fake, uvicorn.Server, threading.Thread]:
    fake = Fake()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    fake.base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(fake.app(), log_level="warning", lifespan="off")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("fake MCP server did not start")
        time.sleep(0.02)
    return fake, server, thread
