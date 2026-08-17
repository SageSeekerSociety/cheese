"""The transport that lets a remote machine reach the metering proxy at all.

A MicroCloud machine cannot open a TCP connection to the meter's CONNECT
listener — measured 2026-08-14, packets to the box's 8444 are dropped before its
NIC — and it cannot be steered any other way, because setting
``ANTHROPIC_BASE_URL`` flips Claude Code out of subscription mode. So its bytes
ride a WebSocket over the gateway path that already carries the connector.

Driven against a REAL listener on loopback: what is under test is byte fidelity
in both directions, and the failures that matter (bytes altered, one direction
never flushed, the dial happening before auth) are exactly the ones a mock
cannot show.

Written synchronously on purpose. ``websocket_connect`` blocks the calling
thread, so a listener sharing the test's event loop can never accept the
connection the test is waiting on — the first version of this file deadlocked
that way. The listener therefore owns a thread and a loop of its own.
"""

import socket
import threading

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token

_PROJECT = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


class _FakeListener:
    """Stands in for the meter's CONNECT listener. Speaks first (so the
    server→client direction is exercised), then echoes upper-cased — a visible
    transform, so a pipe that silently short-circuits cannot pass as success."""

    def __init__(self, greeting: bytes = b"") -> None:
        self.greeting = greeting
        self.received = bytearray()
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_FakeListener":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        with conn:
            if self.greeting:
                conn.sendall(self.greeting)
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                self.received.extend(data)
                try:
                    conn.sendall(data.upper())
                except OSError:
                    return


def _point_at(monkeypatch, port: int) -> None:
    monkeypatch.setattr(settings, "subscription_proxy_host", "127.0.0.1")
    monkeypatch.setattr(settings, "subscription_proxy_connect_port", port)


def test_bytes_cross_in_both_directions_unaltered(client, monkeypatch):
    """The meter runs its own CONNECT handshake through this pipe, so the pipe
    must not parse, buffer or reframe anything."""
    with _FakeListener(greeting=b"hello-from-meter") as listener:
        _point_at(monkeypatch, listener.port)
        token = mint_scoped_token(project_id=_PROJECT)

        with client.websocket_connect(f"/llm/tunnel?token={token}") as ws:
            assert ws.receive_bytes() == b"hello-from-meter"
            ws.send_bytes(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
            assert (
                ws.receive_bytes() == b"CONNECT API.ANTHROPIC.COM:443 HTTP/1.1\r\n\r\n"
            )

        assert bytes(listener.received).startswith(b"CONNECT api.anthropic.com:443")


def test_a_caller_that_cannot_prove_its_project_is_refused(client, monkeypatch):
    """Adding a transport must not widen who may spend the subscription: the
    same scoped token the listener demands as its proxy password is required
    here, and nothing is dialled before it verifies."""
    with _FakeListener() as listener:
        _point_at(monkeypatch, listener.port)

        for query in ("", "?token=", "?token=not-a-real-token"):
            with pytest.raises(Exception):  # noqa: B017 — starlette raises on close
                with client.websocket_connect(f"/llm/tunnel{query}") as ws:
                    ws.receive_bytes()

        # Refused before the dial: an unauthenticated caller must not be able to
        # make the backend open a connection on its behalf.
        assert not listener.received


def test_a_meter_that_is_down_closes_rather_than_hanging(client, monkeypatch):
    """A caller left waiting cannot tell a dead meter from a slow model, so the
    failure has to arrive as a closed socket."""
    # Port 1 has nothing on it; the OS refuses immediately.
    _point_at(monkeypatch, 1)
    token = mint_scoped_token(project_id=_PROJECT)

    with pytest.raises(Exception):  # noqa: B017 — starlette raises on close
        with client.websocket_connect(f"/llm/tunnel?token={token}") as ws:
            ws.receive_bytes()
