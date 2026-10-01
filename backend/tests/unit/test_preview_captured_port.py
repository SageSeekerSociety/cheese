"""Linux native listener/peer evidence for declaration A→B→A interleaving."""

import base64
import hashlib
import inspect
import os
import socket
import threading

import pytest

from app.domain.agent import preview_tunnel as wire


def declare(path, port):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(str(port))
    os.replace(temporary, path)


@pytest.mark.parametrize("kind", [wire.OP_REQ, wire.OP_WS_OPEN])
@pytest.mark.parametrize("fixed", [True, False])
def test_captured_port_cannot_be_validated_against_another_declaration(
    tmp_path, monkeypatch, kind, fixed
):
    listeners = []
    peers = []
    received = []
    servers = []
    stop = threading.Event()
    for _ in range(2):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.05)
        listeners.append(listener)
    a, b = [listener.getsockname()[1] for listener in listeners]
    path = tmp_path / "port"
    declare(path, a)
    source = wire.PortSource(str(path))
    expected = source.instance()  # Real Linux /proc identity, not metadata doubles.
    native_get = source.get
    native_connect = socket.socket.connect

    def capture():
        # Only interleave the serving path's direct port capture. Identity lookup
        # reads real /proc for A; the captured dial port is B; declaration returns A.
        if inspect.currentframe().f_back.f_code.co_name == "instance":
            return native_get()
        declare(path, b)
        port = native_get()
        declare(path, a)
        return port

    def connect(sock, address):
        native_connect(sock, address)
        peers.append(sock.getpeername()[1])

    monkeypatch.setattr(source, "get", capture)
    monkeypatch.setattr(socket.socket, "connect", connect)

    def serve(listener, label):
        while not stop.is_set():
            try:
                app, _ = listener.accept()
            except TimeoutError:
                continue
            with app:
                app.settimeout(2)
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    chunk = app.recv(1)
                    if not chunk:
                        return
                    request.extend(chunk)
                received.append((label, bytes(request)))
                if kind == wire.OP_REQ:
                    body = ("instance-" + label).encode()
                    app.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: "
                        + str(len(body)).encode()
                        + b"\r\n\r\n"
                        + body
                    )
                else:
                    key = next(
                        line.split(b":", 1)[1].strip()
                        for line in request.split(b"\r\n")
                        if line.lower().startswith(b"sec-websocket-key:")
                    )
                    accepted = base64.b64encode(
                        hashlib.sha1(
                            key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
                            usedforsecurity=False,
                        ).digest()
                    )
                    app.sendall(
                        b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                        b"Connection: Upgrade\r\nSec-WebSocket-Accept: "
                        + accepted
                        + b"\r\n\r\n"
                        + b"\x88\x02\x03\xe8"
                    )
            return

    for listener, label in zip(listeners, ["A", "B"], strict=True):
        thread = threading.Thread(target=serve, args=(listener, label))
        thread.start()
        servers.append(thread)
    backend, helper = socket.socketpair()
    backend.settimeout(3)
    session = wire.Session(helper, source)
    events = []
    try:
        meta = {"path": "/"}
        if fixed:
            meta["instance"] = expected
        session._dispatch(kind, 1, wire.encode_meta(meta))
        while True:
            _, data = wire.recv_message(backend)
            event = wire.decode(data)
            events.append(event)
            if event[0] in (wire.OP_ERR, wire.OP_END, wire.OP_CLOSE):
                break
        print(
            {
                "kind": kind,
                "fixed": fixed,
                "A": a,
                "B": b,
                "actual_peers": peers,
                "received_by": [label for label, _ in received],
                "events": [
                    (op, stream, payload.decode(errors="replace"))
                    for op, stream, payload in events
                ],
            }
        )
        if fixed:
            assert any(event[0] == wire.OP_ERR for event in events), (
                "fixed A reached B without rejection"
            )
            assert not peers and not received
        else:
            assert peers == [b] and received[0][0] == "B"
            assert not any(event[0] == wire.OP_ERR for event in events)
    finally:
        session._stop()
        stop.set()
        for thread in servers:
            thread.join(3)
            assert not thread.is_alive()
        for listener in listeners:
            listener.close()
        helper.close()
        backend.close()
