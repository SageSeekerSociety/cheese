"""A browser's socket to a backend that has handed its running work over is
ended with 1012, so the browser reconnects to the backend that now has it.
The connections that reach a backend past app-router stay up.

A real uvicorn and a real WebSocket client: what is checked is what a browser
would see.
"""

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from app.core.ws_handover import BusinessSockets, EndBusinessSocketsAtHandover


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def served():
    sockets = BusinessSockets()
    handlers_left: list[str] = []

    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_text(await websocket.receive_text())
        except WebSocketDisconnect:
            handlers_left.append(websocket.url.path)

    app = EndBusinessSocketsAtHandover(
        Starlette(
            routes=[
                WebSocketRoute("/rooms/live", echo),
                WebSocketRoute("/notifications/live", echo),
                WebSocketRoute("/llm/tunnel", echo),
            ]
        ),
        sockets,
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    loop_ready = threading.Event()
    holder = {}

    def run() -> None:
        loop = asyncio.new_event_loop()
        holder["loop"] = loop
        loop_ready.set()
        loop.run_until_complete(server.serve())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    loop_ready.wait(5)
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline, "the server never came up"
        time.sleep(0.05)

    def hand_over() -> None:
        asyncio.run_coroutine_threadsafe(sockets.end_all(), holder["loop"]).result(5)

    try:
        yield f"ws://127.0.0.1:{port}", hand_over, handlers_left
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def test_browser_sockets_end_with_1012_and_their_handlers_leave(served):
    base, hand_over, handlers_left = served
    paths = ["/rooms/live", "/notifications/live"]
    browsers = [connect(base + path) for path in paths]
    for browser in browsers:
        browser.send("hello")
        assert browser.recv(timeout=5) == "hello"

    hand_over()

    for browser in browsers:
        with pytest.raises(ConnectionClosed) as closed:
            browser.recv(timeout=5)
        assert closed.value.rcvd is not None
        assert closed.value.rcvd.code == 1012
    deadline = time.monotonic() + 5
    while sorted(handlers_left) != sorted(paths):
        assert time.monotonic() < deadline, handlers_left
        time.sleep(0.05)


def test_a_connection_past_app_router_stays_up(served):
    base, hand_over, _ = served
    with connect(base + "/llm/tunnel") as tunnel:
        hand_over()
        tunnel.send("still here")
        assert tunnel.recv(timeout=5) == "still here"
