"""A handled exception on a WebSocket route closes the socket; it answers nothing.

Starlette sends whatever an exception handler returns down the connection the
exception came from. On an accepted socket that is an HTTP response start,
which uvicorn refuses as 「Expected ASGI message 'websocket.send' or
'websocket.close', but got 'websocket.http.response.start'」 and logs as an
application error — 1143 times in the connection owner on 2026-09-19, for one
machine whose link died between `accept` and the welcome frame.
"""

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.core.errors import ForbiddenError, register_exception_handlers
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
    sent: list[dict] = []
    inbox = iter([{"type": "websocket.connect"}, {"type": "websocket.disconnect"}])

    async def receive() -> dict:
        return next(inbox)

    async def send(message: dict) -> None:
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
