"""The preview helper on a machine with a platform socket.

The rules, as a person would state them: inside the sandbox it dials
`preview.sock` with no token of its own; the host-side relay outside the
sandbox attaches the session's credential on the upstream connection; a socket
that does not answer is waited out like any other absence.
"""

import os
import socketserver
import tempfile
import threading

import pytest

from app.domain.agent.preview_tunnel import CAPS_HEADER, open_ws


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@pytest.fixture
def preview_socket():
    seen_headers = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            lines = []
            while True:
                line = self.rfile.readline().decode("latin-1").rstrip("\r\n")
                if not line:
                    break
                lines.append(line)
            seen_headers.append(lines)
            self.wfile.write(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n\r\n"
            )

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "preview.sock")
    server = _Server(path, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield path, seen_headers
    server.shutdown()
    server.server_close()


def test_preview_socket_dials_without_a_token_inside_the_sandbox(preview_socket):
    path, seen_headers = preview_socket
    sock, _ = open_ws(
        "ws://127.0.0.1:9/preview/tunnel",
        unix_socket=path,
        headers={CAPS_HEADER: "http-stream-v1"},
    )
    sock.close()
    assert len(seen_headers) == 1
    assert not any(
        line.lower().startswith("authorization:") for line in seen_headers[0]
    )


def test_a_socket_that_does_not_answer_is_waited_out():
    with pytest.raises(OSError):
        open_ws(
            "ws://127.0.0.1:9/preview/tunnel",
            unix_socket="/tmp/no-such-preview.sock",
            timeout=1,
        )
