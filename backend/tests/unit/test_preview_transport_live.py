"""Real viewer/tunnel/app sockets, not a content-host authorization proof.

The fixture invokes the authorized relay directly. The real machine route still
checks a locally minted scoped token; production grant/cookie checks are outside
this transport test.
"""

import gzip
import http.client
import socket
import threading
import time
import uuid
from contextlib import contextmanager

import pytest
import uvicorn
from fastapi import FastAPI, Request

from app.api.routes import app_preview as route
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewHub


def _wait_for(predicate, label):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, label
        time.sleep(0.01)


@contextmanager
def _relay(port_file, monkeypatch):
    topic = uuid.uuid4()
    seat = "transport-fixture"
    monkeypatch.setattr(route, "preview_hub", PreviewHub())
    application = FastAPI()
    application.include_router(route.tunnel_router)

    @application.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def viewer(request: Request):
        return await route.relay_http(topic, seat, request)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(application, log_level="error", lifespan="off")
    )
    backend = threading.Thread(
        target=lambda: server.run(sockets=[listener]), daemon=True
    )
    backend.start()
    _wait_for(lambda: server.started, "backend start")
    token = mint_scoped_token(
        project_id=str(uuid.uuid4()), topic_id=str(topic), agent_handle=seat
    )
    tunnel, answered = wire.open_ws(
        f"ws://127.0.0.1:{port}/preview/tunnel?token={token}",
        headers={wire.CAPS_HEADER: ",".join(sorted(wire.CAPABILITIES))},
    )
    capabilities = frozenset(answered[wire.CAPS_HEADER].split(","))
    assert capabilities == wire.CAPABILITIES
    session = wire.Session(
        tunnel, wire.PortSource(str(port_file)), capabilities=capabilities
    )
    helper = threading.Thread(target=session.serve, daemon=True)
    helper.start()
    try:
        yield port, session
    finally:
        try:
            tunnel.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        tunnel.close()
        helper.join(6)
        server.should_exit = True
        backend.join(6)
        assert not helper.is_alive()
        assert not backend.is_alive()
        assert session.drained
        assert not session._streams


def _read_head(connection, *, with_headers=False):
    head = bytearray()
    while b"\r\n\r\n" not in head:
        chunk = connection.recv(4096)
        assert chunk, "upstream request ended before headers"
        head.extend(chunk)
    lines = bytes(head).split(b"\r\n")
    if with_headers:
        headers = {
            key.strip().lower().decode(): value.strip().decode()
            for line in lines[1:]
            if b":" in line
            for key, value in [line.split(b":", 1)]
        }
        return lines[0].decode(), headers
    return lines[0].decode()


@pytest.mark.parametrize("phase", ["before_headers", "after_headers", "first_chunk"])
def test_browser_disconnect_reaches_app_and_joins_helper(phase, tmp_path, monkeypatch):
    accepted = threading.Event()
    finished = threading.Event()
    observed = {}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    port_file = tmp_path / "preview.port"
    port_file.write_text(str(listener.getsockname()[1]))

    def app_peer():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(6)
                observed["request"] = _read_head(connection)
                if phase != "before_headers":
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                        b"Transfer-Encoding: chunked\r\n\r\n"
                    )
                if phase == "first_chunk":
                    connection.sendall(b"c\r\ndata: first\n\r\n")
                accepted.set()
                try:
                    observed["disconnect"] = connection.recv(1)
                except ConnectionResetError:
                    observed["disconnect"] = b""
        except Exception as exc:
            observed["error"] = repr(exc)
        finally:
            finished.set()

    app = threading.Thread(target=app_peer, daemon=True)
    app.start()
    try:
        with _relay(port_file, monkeypatch) as (port, session):
            with socket.create_connection(("127.0.0.1", port), timeout=4) as browser:
                browser.sendall(
                    b"GET /events?a=1&a=2 HTTP/1.1\r\nHost: localhost\r\n"
                    b"Connection: close\r\n\r\n"
                )
                assert accepted.wait(4), observed
                with session._lock:
                    states = list(session._streams.values())
                assert len(states) == 1
                received = bytearray()
                if phase != "before_headers":
                    while b"\r\n\r\n" not in received:
                        chunk = browser.recv(4096)
                        assert chunk
                        received.extend(chunk)
                    assert b"200 OK" in received
                if phase == "first_chunk":
                    while b"data: first\n" not in received:
                        chunk = browser.recv(4096)
                        assert chunk
                        received.extend(chunk)
                assert not finished.is_set(), "app has not sent END"
                browser.shutdown(socket.SHUT_RDWR)
            assert finished.wait(6), observed
            assert "error" not in observed, observed
            assert observed["request"] == "GET /events?a=1&a=2 HTTP/1.1"
            assert observed["disconnect"] == b""
            for state in states:
                assert state.done.wait(5)
                state.worker.join(1)
                assert not state.worker.is_alive()
                assert state.socket.fileno() == -1
            _wait_for(lambda: not session._streams, "returned helper capacity")
    finally:
        listener.close()
        app.join(7)
        assert not app.is_alive()


def test_representation_headers_ranges_and_raw_query_reach_viewer(
    tmp_path, monkeypatch
):
    zipped = gzip.compress(b"representation bytes")
    seen = []
    errors = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    port_file = tmp_path / "preview.port"
    port_file.write_text(str(listener.getsockname()[1]))

    def app_peer():
        try:
            for _ in range(4):
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(5)
                    line, request_headers = _read_head(connection, with_headers=True)
                    seen.append(line)
                    method, path, _ = line.split(" ")
                    if path.startswith("/gzip"):
                        status, body, extra = (
                            "200 OK",
                            zipped,
                            b"Content-Encoding: gzip\r\n",
                        )
                    elif path == "/range":
                        assert request_headers.get("range") == "bytes=2-4"
                        status, body, extra = (
                            "206 Partial Content",
                            b"234",
                            b"Content-Range: bytes 2-4/10\r\n",
                        )
                    elif path == "/unsatisfied":
                        assert request_headers.get("range") == "bytes=99-100"
                        status, body, extra = (
                            "416 Range Not Satisfiable",
                            b"",
                            b"Content-Range: bytes */10\r\n",
                        )
                    else:
                        status, body, extra = "200 OK", b"", b""
                    length = 12345 if method == "HEAD" else len(body)
                    connection.sendall(
                        f"HTTP/1.1 {status}\r\nContent-Length: {length}\r\n".encode()
                        + extra
                        + b"Connection: close\r\n\r\n"
                        + body
                    )
        except Exception as exc:
            errors.append(repr(exc))

    app = threading.Thread(target=app_peer, daemon=True)
    app.start()
    try:
        with _relay(port_file, monkeypatch) as (port, _):
            for method, path in [
                ("GET", "/gzip?a=1&a=2&escaped=%2F"),
                ("HEAD", "/head"),
                ("GET", "/range"),
                ("GET", "/unsatisfied"),
            ]:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
                try:
                    request_headers = {}
                    if path == "/range":
                        request_headers["Range"] = "bytes=2-4"
                    elif path == "/unsatisfied":
                        request_headers["Range"] = "bytes=99-100"
                    connection.request(method, path, headers=request_headers)
                    response = connection.getresponse()
                    body = response.read()
                    if path.startswith("/gzip"):
                        assert body == zipped
                        assert response.getheader("content-encoding") == "gzip"
                        assert response.getheader("content-length") == str(len(zipped))
                    elif method == "HEAD":
                        assert body == b""
                        assert response.getheader("content-length") == "12345"
                    elif path == "/range":
                        assert response.status == 206 and body == b"234"
                        assert response.getheader("content-range") == "bytes 2-4/10"
                    else:
                        assert response.status == 416 and body == b""
                        assert response.getheader("content-range") == "bytes */10"
                finally:
                    connection.close()
        assert seen[0] == "GET /gzip?a=1&a=2&escaped=%2F HTTP/1.1"
        assert not errors, errors
    finally:
        listener.close()
        app.join(6)
        assert not app.is_alive()
