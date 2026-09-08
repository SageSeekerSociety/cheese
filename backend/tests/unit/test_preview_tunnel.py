"""运行环境预览's machine half, against a real server on a real loopback port.

The helper is shipped to a machine the platform does not own, so the two things
worth pinning are what it fetches and — much more important — what it is *able*
to fetch. It dials the port a process on that machine wrote to a file, and there
is no field on the wire that can move it: that is the whole security story of
handing a room a window into someone's laptop, so a test has to be able to fail
when it stops being true.
"""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain.agent import preview_tunnel as wire


class _Recorder(BaseHTTPRequestHandler):
    body = b"served"

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's contract
        self.server.seen.append(self.path)  # type: ignore[attr-defined]
        payload = self.server.body  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        return


def _server(body: bytes = b"served") -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    httpd.seen = []  # type: ignore[attr-defined]
    httpd.body = body  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class _Peer:
    """The backend end of the tunnel, spoken over a socket pair."""

    def __init__(self, port_file: str) -> None:
        self.backend, machine = socket.socketpair()
        self.backend.settimeout(10)
        self.session = wire.Session(machine, wire.PortSource(port_file))
        self.thread = threading.Thread(target=self.session.serve, daemon=True)
        self.thread.start()

    def send(self, op: int, stream: int, payload: bytes = b"") -> None:
        wire.send_frame(self.backend, wire.encode(op, stream, payload))

    def recv(self) -> tuple[int, int, bytes]:
        message = wire.recv_message(self.backend)
        assert message is not None, "the machine closed the tunnel"
        return wire.decode(message[1])

    def close(self) -> None:
        self.backend.close()
        self.thread.join(timeout=5)


@pytest.fixture
def port_file(tmp_path):
    return tmp_path / "cheese-preview.port"


def test_it_fetches_from_the_declared_port(port_file):
    app = _server(b"hello from the app")
    port_file.write_text(str(app.server_address[1]))
    peer = _Peer(str(port_file))
    try:
        peer.send(
            wire.OP_REQ,
            7,
            wire.encode_meta({"method": "GET", "path": "/index.html", "headers": []}),
        )
        op, stream, payload = peer.recv()
        assert (op, stream) == (wire.OP_RESP, 7)
        meta, first = wire.decode_meta(payload)
        assert meta["status"] == 200, meta

        body = bytearray(first)
        while True:
            op, _, payload = peer.recv()
            if op == wire.OP_END:
                break
            assert op == wire.OP_DATA, payload
            body.extend(payload)
        assert bytes(body) == b"hello from the app"
        assert app.seen == ["/index.html"], app.seen  # type: ignore[attr-defined]
    finally:
        peer.close()
        app.shutdown()


@pytest.mark.parametrize("legacy_base", ["", "/api/topics/old-topic/app/\n"])
def test_app_receives_original_html_asset_and_api_paths(port_file, legacy_base):
    app = _server(b"app")
    port_file.write_text(f"{app.server_address[1]}\n{legacy_base}")
    peer = _Peer(str(port_file))
    try:
        for stream, path in enumerate(
            ["/", "/src/main.js?import", "/api/items?a=1&a=2"], start=1
        ):
            peer.send(wire.OP_REQ, stream, wire.encode_meta({"path": path}))
            op, _, payload = peer.recv()
            assert op == wire.OP_RESP, payload
            while peer.recv()[0] != wire.OP_END:
                pass
        assert app.seen == ["/", "/src/main.js?import", "/api/items?a=1&a=2"]
    finally:
        peer.close()
        app.shutdown()


def test_the_port_comes_from_the_machine_not_the_wire(port_file):
    """Re-read per stream, so an agent restarting its server on another port is
    followed — and, the half that matters, so nothing the backend sends can
    choose an address. The frame carries a path; it never carries a host."""
    first_app = _server(b"first")
    second_app = _server(b"second")
    port_file.write_text(str(first_app.server_address[1]))
    peer = _Peer(str(port_file))
    try:
        peer.send(
            wire.OP_REQ,
            1,
            wire.encode_meta({"method": "GET", "path": "/", "headers": []}),
        )
        while peer.recv()[0] != wire.OP_END:
            pass

        # The only way to move the helper: rewrite the file on the machine.
        port_file.write_text(str(second_app.server_address[1]))
        peer.send(
            wire.OP_REQ,
            2,
            wire.encode_meta({"method": "GET", "path": "/", "headers": []}),
        )
        while peer.recv()[0] != wire.OP_END:
            pass

        assert first_app.seen == ["/"], first_app.seen  # type: ignore[attr-defined]
        assert second_app.seen == ["/"], second_app.seen  # type: ignore[attr-defined]
    finally:
        peer.close()
        first_app.shutdown()
        second_app.shutdown()


def test_an_absolute_url_in_the_request_does_not_redirect_the_dial(port_file):
    """A request-target is a path. Even handed a whole URL naming another host,
    the connection still goes to the declared loopback port — the helper resolves
    no address from the frame at all."""
    app = _server()
    port_file.write_text(str(app.server_address[1]))
    peer = _Peer(str(port_file))
    try:
        peer.send(
            wire.OP_REQ,
            3,
            wire.encode_meta(
                {"method": "GET", "path": "http://127.0.0.1:9/x", "headers": []}
            ),
        )
        op, _, payload = peer.recv()
        assert op == wire.OP_RESP, payload
        assert app.seen == ["http://127.0.0.1:9/x"], app.seen  # type: ignore[attr-defined]
    finally:
        peer.close()
        app.shutdown()


def test_no_declared_port_is_an_error_on_the_stream_not_a_dead_socket(port_file):
    """Nothing has run `cheese serve` here yet. The browser has to be told, and
    the tunnel has to survive it — one unanswerable request must not take down
    the connection every other preview request rides."""
    peer = _Peer(str(port_file))
    try:
        peer.send(
            wire.OP_REQ,
            4,
            wire.encode_meta({"method": "GET", "path": "/", "headers": []}),
        )
        op, stream, payload = peer.recv()
        assert (op, stream) == (wire.OP_ERR, 4)
        assert b"no preview port" in payload, payload

        app = _server(b"now serving")
        port_file.write_text(str(app.server_address[1]))
        peer.send(
            wire.OP_REQ,
            5,
            wire.encode_meta({"method": "GET", "path": "/", "headers": []}),
        )
        assert peer.recv()[0] == wire.OP_RESP
        app.shutdown()
    finally:
        peer.close()


def test_a_dead_app_behind_a_live_tunnel_is_reported_not_silent(port_file):
    """The tunnel is up and the agent's dev server has exited — the panel must be
    able to say which of the two is missing."""
    app = _server()
    port = app.server_address[1]
    app.shutdown()
    app.server_close()
    port_file.write_text(str(port))
    peer = _Peer(str(port_file))
    try:
        peer.send(
            wire.OP_REQ,
            6,
            wire.encode_meta({"method": "GET", "path": "/", "headers": []}),
        )
        op, stream, payload = peer.recv()
        assert (op, stream) == (wire.OP_ERR, 6), payload
    finally:
        peer.close()


def test_it_relays_a_websocket_to_the_declared_port(port_file):
    """A dev server pushes reloads over a WebSocket, so the helper has to speak
    one to the app as well as over the tunnel. Against a real server, because the
    handshake and the masking rules are exactly what a hand-written relay gets
    wrong, and the symptom is a page that reloads forever."""
    from websockets.sync.server import serve

    seen = []

    def echo(connection):
        seen.append(connection.request.path)
        for message in connection:
            connection.send(message)

    server = serve(echo, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port_file.write_text(f"{server.socket.getsockname()[1]}\n")
    peer = _Peer(str(port_file))
    try:
        peer.send(wire.OP_WS_OPEN, 8, wire.encode_meta({"path": "/hmr", "headers": []}))
        op, stream, _payload = peer.recv()
        assert (op, stream) == (wire.OP_WS_OK, 8)

        peer.send(wire.OP_WS_MSG, 8, bytes([wire.WS_TEXT]) + b"reload")
        op, stream, payload = peer.recv()
        assert (op, stream) == (wire.OP_WS_MSG, 8)
        assert payload == bytes([wire.WS_TEXT]) + b"reload", payload
        # The handshake can finish before the server handler runs. Its echo
        # proves it has recorded the path; WS_OK alone does not.
        assert seen == ["/hmr"]

        peer.send(wire.OP_WS_MSG, 8, bytes([wire.WS_BINARY]) + b"\x00\xff")
        _op, _stream, payload = peer.recv()
        assert payload == bytes([wire.WS_BINARY]) + b"\x00\xff", payload
    finally:
        peer.close()
        server.shutdown()
