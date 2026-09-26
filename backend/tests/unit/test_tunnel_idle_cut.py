"""A pipe whose far end died silently must be cut, not waited on forever.

2026-09-25: a deploy recreated the meter while a turn's request was in flight;
every hop stayed ESTABLISHED, nothing ever closed, and claude waited minutes on
a response that could not arrive. Both halves of the tunnel now cut a pair that
has carried nothing for a while — and must NOT cut one that is still streaming.
"""

import asyncio
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes import llm_tunnel
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import machine_tunnel

_CONNECT_HEAD = (
    b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com\r\n\r\n"
)


def _server_frame(payload: bytes) -> bytes:
    # Unmasked, as a server sends it.
    assert len(payload) < 126
    return bytes([0x82, len(payload)]) + payload


def _run_helper(monkeypatch, idle_s: float):
    """handle_connection over socketpairs: (claude side, backend side, thread)."""
    monkeypatch.setattr(machine_tunnel, "_IDLE_CHECK_S", 0.02)
    claude, local = socket.socketpair()
    backend, ws = socket.socketpair()
    monkeypatch.setattr(
        machine_tunnel, "_open_with_patience", lambda *a, **k: (ws, "tok")
    )
    thread = threading.Thread(
        target=machine_tunnel.handle_connection,
        args=(local, "ws://x/llm/tunnel", "tok"),
        kwargs={"idle_timeout_s": idle_s},
        daemon=True,
    )
    thread.start()
    claude.sendall(_CONNECT_HEAD)
    head = machine_tunnel.recv_message(backend)
    assert head is not None and head.startswith(b"CONNECT ")
    return claude, backend, thread


def test_helper_cuts_a_pair_that_went_silent(monkeypatch):
    claude, backend, thread = _run_helper(monkeypatch, idle_s=0.3)
    started = time.monotonic()
    claude.settimeout(5)
    assert claude.recv(1) == b""  # claude sees the close and can retry
    assert time.monotonic() - started < 3
    thread.join(timeout=3)
    assert not thread.is_alive()
    claude.close()
    backend.close()


def test_helper_keeps_a_pair_that_is_still_streaming(monkeypatch):
    claude, backend, thread = _run_helper(monkeypatch, idle_s=0.3)
    claude.settimeout(2)
    for _ in range(12):  # 1.2s of stream, four times the idle limit
        backend.sendall(_server_frame(b"delta"))
        assert claude.recv(5) == b"delta"
        time.sleep(0.1)
    assert thread.is_alive()
    # ...and once the stream stops, the silence is cut after all.
    claude.settimeout(5)
    assert claude.recv(1) == b""
    thread.join(timeout=3)
    assert not thread.is_alive()
    claude.close()
    backend.close()


class _SilentMeter:
    """Accepts and reads, never answers — a meter that died under the pipe."""

    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self.conns: list[socket.socket] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        self.conns.append(conn)

    def close(self) -> None:
        for conn in self.conns:
            conn.close()
        self._sock.close()


@pytest.fixture
def silent_meter(monkeypatch):
    meter = _SilentMeter()
    monkeypatch.setattr(settings, "subscription_proxy_host", "127.0.0.1")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", meter.port)
    yield meter
    meter.close()


def test_backend_closes_a_tunnel_whose_meter_went_silent(silent_meter, monkeypatch):
    from app.llm_tunnel_app import app

    monkeypatch.setattr(llm_tunnel, "IDLE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(llm_tunnel, "_IDLE_CHECK_S", 0.02)
    token = mint_scoped_token(project_id="7c9e6679-7425-40de-944b-e07fc1f90ae7")
    with TestClient(app) as client:
        with client.websocket_connect(f"/llm/tunnel?token={token}") as ws:
            ws.send_bytes(_CONNECT_HEAD)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_bytes()
    assert closed.value.code == 1011


def test_backend_idle_wait_is_pushed_back_by_traffic():
    async def scenario() -> float:
        activity = llm_tunnel._Activity()
        waiter = asyncio.create_task(llm_tunnel._until_idle(activity, 0.2, 0.01))
        started = time.monotonic()
        for _ in range(6):
            await asyncio.sleep(0.1)
            assert not waiter.done()
            activity.touch()
        await asyncio.wait_for(waiter, timeout=2)
        return time.monotonic() - started

    assert asyncio.run(scenario()) >= 0.6
