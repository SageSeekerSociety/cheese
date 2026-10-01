"""Admission and slow-peer isolation against independent loopback sockets."""

import base64
import hashlib
import socket
import socketserver
import threading
import time

from app.domain.agent import preview_tunnel as wire
from tests.unit.test_preview_tunnel import _Peer


def _wait_for(predicate, message):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, message
        time.sleep(0.01)


def _join_state(state):
    assert state.done.wait(5)
    state.worker.join(1)
    assert not state.worker.is_alive()
    assert state.writer is None or not state.writer.is_alive()
    assert state.socket.fileno() == -1


class _App(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(7)
        head = bytearray()
        while b"\r\n\r\n" not in head:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            head.extend(chunk)
        path = bytes(head).split(b"\r\n")[0].split(b" ")[1]
        if path == b"/ok":
            self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            return
        if path == b"/ws":
            headers = dict(
                line.split(b":", 1)
                for line in bytes(head).split(b"\r\n")[1:]
                if b":" in line
            )
            key = next(
                v.strip()
                for k, v in headers.items()
                if k.lower() == b"sec-websocket-key"
            )
            accept = base64.b64encode(
                hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
            )
            self.request.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
            )
            self.server.ws_connected.set()
            # Do not read WS data until the sibling HTTP response has arrived.
            if not self.server.read_ws.wait(5):
                return
        with self.server.lock:
            self.server.accepted += 1
        try:
            while self.request.recv(65536):
                pass
        except ConnectionResetError:
            pass
        else:
            with self.server.lock:
                self.server.eofs += 1


def _app(port_file):
    app = _App(("127.0.0.1", 0), _Handler)
    app.lock = threading.Lock()
    app.accepted = 0
    app.eofs = 0
    app.ws_connected = threading.Event()
    app.read_ws = threading.Event()
    port_file.write_text(str(app.server_address[1]))
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    return app, thread


def _http_ok(peer, sid):
    peer.send(wire.OP_REQ, sid, wire.encode_meta({"path": "/ok"}))
    body = bytearray()
    while True:
        op, stream, payload = peer.recv()
        if stream != sid:
            continue
        if op == wire.OP_RESP:
            meta, first = wire.decode_meta(payload)
            assert meta["status"] == 200
            body.extend(first)
        elif op == wire.OP_DATA:
            body.extend(payload)
        elif op == wire.OP_END:
            break
        else:
            raise AssertionError((op, payload))
    assert body == b"ok"


def test_http_admission_rejects_excess_then_returns_capacity(tmp_path):
    port_file = tmp_path / "preview.port"
    app, thread = _app(port_file)
    peer = _Peer(str(port_file), capabilities=wire.CAPABILITIES)
    states = []
    try:
        for sid in range(1, 17):
            peer.send(wire.OP_REQ, sid, wire.encode_meta({"path": "/hold"}))
        _wait_for(lambda: app.accepted == 16, "all admitted app sockets")
        with peer.session._lock:
            states = list(peer.session._streams.values())
        peer.send(wire.OP_REQ, 17, wire.encode_meta({"path": "/ok"}))
        assert peer.recv() == (wire.OP_ERR, 17, b"preview streams busy")
        peer.send(wire.OP_CLOSE, 1)
        _join_state(states[0])
        _wait_for(lambda: app.eofs >= 1, "independent app EOF")
        _http_ok(peer, 18)
        for sid in range(2, 17):
            peer.send(wire.OP_CLOSE, sid)
        for state in states[1:]:
            _join_state(state)
        _wait_for(lambda: app.eofs == 16, "all canceled app sockets EOF")
    finally:
        peer.close()
        app.shutdown()
        app.server_close()
        thread.join(1)
        assert not thread.is_alive()


def test_ws_admission_is_independent_of_http_and_returns_capacity(tmp_path):
    port_file = tmp_path / "preview.port"
    app, thread = _app(port_file)
    app.read_ws.set()
    peer = _Peer(str(port_file), capabilities=wire.CAPABILITIES)
    try:
        for sid in range(1, 17):
            peer.send(wire.OP_WS_OPEN, sid, wire.encode_meta({"path": "/ws"}))
            op, stream, _ = peer.recv()
            assert (op, stream) == (wire.OP_WS_OK, sid)
        _wait_for(lambda: app.accepted == 16, "sixteen WS app sockets")
        with peer.session._lock:
            states = dict(peer.session._streams)
        peer.send(wire.OP_WS_OPEN, 17, wire.encode_meta({"path": "/ws"}))
        assert peer.recv() == (wire.OP_ERR, 17, b"preview streams busy")
        _http_ok(peer, 18)
        peer.send(wire.OP_CLOSE, 1)
        _join_state(states[1])
        peer.send(wire.OP_WS_OPEN, 19, wire.encode_meta({"path": "/ws"}))
        while True:
            op, sid, _ = peer.recv()
            if sid == 19:
                assert op == wire.OP_WS_OK
                break
        with peer.session._lock:
            replacement = peer.session._streams[19]
        for sid in [*range(2, 17), 19]:
            peer.send(wire.OP_CLOSE, sid)
        for state in [*(states[sid] for sid in range(2, 17)), replacement]:
            _join_state(state)
        _wait_for(lambda: app.eofs == 17, "all WS app sockets EOF")
    finally:
        peer.close()
        app.shutdown()
        app.server_close()
        thread.join(1)
        assert not thread.is_alive()


def test_slow_ws_outbox_does_not_block_http_sibling(tmp_path):
    port_file = tmp_path / "preview.port"
    app, thread = _app(port_file)
    peer = _Peer(str(port_file), capabilities=wire.CAPABILITIES)
    try:
        peer.send(wire.OP_WS_OPEN, 1, wire.encode_meta({"path": "/ws"}))
        assert peer.recv()[0] == wire.OP_WS_OK
        assert app.ws_connected.wait(3)
        with peer.session._lock:
            state = peer.session._streams[1]
        state.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        for _ in range(40):
            peer.send(wire.OP_WS_MSG, 1, bytes([wire.WS_BINARY]) + b"x" * 65536)
        _http_ok(peer, 2)
        app.read_ws.set()
        _join_state(state)
        _wait_for(lambda: app.eofs == 1, "slow WS peer observes EOF")
    finally:
        app.read_ws.set()
        peer.close()
        app.shutdown()
        app.server_close()
        thread.join(1)
        assert not thread.is_alive()


def test_shared_tunnel_write_deadline_closes_app_and_joins_session(
    tmp_path, monkeypatch
):
    port_file = tmp_path / "preview.port"
    app, thread = _app(port_file)
    peer = _Peer(str(port_file), capabilities=wire.CAPABILITIES)
    monkeypatch.setattr(wire, "_WRITE_TIMEOUT_S", 0.2)
    try:
        peer.send(wire.OP_REQ, 1, wire.encode_meta({"path": "/hold"}))
        _wait_for(lambda: app.accepted == 1, "app waiting before headers")
        with peer.session._lock:
            state = peer.session._streams[1]
        peer.session._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        blocked = threading.Thread(
            target=lambda: peer.session.send(wire.OP_DATA, 1, b"x" * (8 * 1024 * 1024)),
            daemon=True,
        )
        blocked.start()
        # Backend deliberately never reads the shared tunnel response.
        blocked.join(3)
        assert not blocked.is_alive()
        peer.thread.join(3)
        assert not peer.thread.is_alive()
        _join_state(state)
        _wait_for(lambda: app.eofs == 1, "app EOF after physical tunnel shutdown")
        assert peer.session._sock.fileno() == -1
        assert peer.session.drained
    finally:
        peer.close()
        app.shutdown()
        app.server_close()
        thread.join(1)
        assert not thread.is_alive()
