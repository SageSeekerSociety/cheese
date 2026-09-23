"""A handled exception on a WebSocket route closes the socket; it answers nothing.

Starlette sends whatever an exception handler returns down the connection the
exception came from. On an accepted socket that is an HTTP response start,
which uvicorn refuses as 「Expected ASGI message 'websocket.send' or
'websocket.close', but got 'websocket.http.response.start'」 and logs as an
application error — 1143 times in the connection owner on 2026-09-19, for one
machine whose link died between `accept` and the welcome frame.
"""

import asyncio
import socket
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.types import Message
from websockets.asyncio.client import connect

from app.core.errors import ForbiddenError, register_exception_handlers
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.device_hub import DeviceOffline


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.websocket("/link")
    async def _link(websocket: WebSocket) -> None:
        await websocket.accept()
        # The welcome frame finding the peer gone, as attach_device reports it.
        raise DeviceOffline("machine-7")

    @app.websocket("/refused")
    async def _refused(websocket: WebSocket) -> None:
        raise ForbiddenError("not yours")

    @app.get("/http")
    async def _http() -> dict:
        raise DeviceOffline("machine-7")

    return app


async def _drive(app: FastAPI, path: str) -> list[str]:
    sent: list[Message] = []
    inbox = iter([{"type": "websocket.connect"}, {"type": "websocket.disconnect"}])

    async def receive() -> dict:
        return next(inbox)

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "ws",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 80),
        "subprotocols": [],
    }
    await app(scope, receive, send)
    return [message["type"] for message in sent]


@pytest.mark.anyio
async def test_a_handled_failure_after_accept_closes_the_socket() -> None:
    assert await _drive(_app(), "/link") == ["websocket.accept", "websocket.close"]


@pytest.mark.anyio
async def test_a_refusal_before_accept_is_still_a_close() -> None:
    assert await _drive(_app(), "/refused") == ["websocket.close"]


def test_the_same_failure_over_http_keeps_its_answer() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    response = client.get("/http")
    assert response.status_code == 409
    assert response.headers["X-Device-Id"] == "machine-7"


@pytest.mark.anyio
async def test_forge_tunnel_cleanup_accepts_an_already_disconnected_peer(monkeypatch):
    from app.api.routes.forge_token import forge_tunnel

    project_id = uuid.uuid4()
    binding = SimpleNamespace(
        kind="github_app",
        url="https://github.com/team/repo",
        api_url="https://api.github.com",
    )
    monkeypatch.setattr(
        "app.domain.project.forge.binding_for_project", AsyncMock(return_value=binding)
    )
    inbox = iter(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.disconnect", "code": 1006},
        ]
    )
    sent = []

    async def receive():
        return next(inbox)

    async def send(message):
        sent.append(message["type"])
        if message["type"] == "websocket.close":
            raise OSError("peer already disconnected")

    websocket = WebSocket({"type": "websocket"}, receive, send)
    db = AsyncMock()
    await forge_tunnel(
        project_id, websocket, mint_scoped_token(project_id=str(project_id)), db
    )
    db.rollback.assert_awaited_once()
    assert sent == ["websocket.accept", "websocket.close"]


@pytest.mark.anyio
async def test_peer_disconnect_before_accept_does_not_crash_the_server() -> None:
    entered = asyncio.Event()
    disconnected = asyncio.Event()
    finished = asyncio.Event()
    failures: list[Exception] = []

    async def app(scope, receive, send):
        await receive()
        if scope["path"] == "/healthy":
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.send", "text": "ready"})
            await receive()
            return
        entered.set()
        await disconnected.wait()
        try:
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})
        except OSError:
            # A departed peer is a transport disconnect, not an application bug.
            pass
        except Exception as exc:
            failures.append(exc)
        finally:
            finished.set()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                b"GET /aborted HTTP/1.1\r\nHost: localhost\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            await writer.drain()
            await entered.wait()
            writer.transport.abort()
            while server.server_state.connections:
                await asyncio.sleep(0.01)
            disconnected.set()
            await finished.wait()
            assert not failures
            async with connect(f"ws://127.0.0.1:{port}/healthy") as websocket:
                assert await websocket.recv() == "ready"
    finally:
        disconnected.set()
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
