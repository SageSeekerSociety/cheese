"""The preview helper on a machine with a platform socket.

The rules, as a person would state them: each connection it opens is opened
on a credential asked of the socket for it, not on a token kept in a file; a
socket that does not answer is waited out like any other absence.
"""

import json
import os
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from app.domain.agent.preview_tunnel import PreviewError, SocketTokenSource


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("socket", 0)


@pytest.fixture
def platform_socket():
    asked = []
    answers = iter(["first", "second"])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            raw = json.dumps({"token": next(answers), "expires_in": 60}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "platform.sock")
    server = _Server(path, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield path, asked
    server.shutdown()
    server.server_close()


def test_each_connection_is_opened_on_a_credential_asked_for_it(platform_socket):
    path, asked = platform_socket
    tokens = SocketTokenSource(path)
    assert [tokens.get(), tokens.get()] == ["first", "second"]
    assert asked == ["/credential", "/credential"]


def test_a_socket_that_does_not_answer_is_waited_out():
    with pytest.raises(PreviewError) as raised:
        SocketTokenSource("/tmp/no-such-platform.sock", timeout=1).get()
    assert raised.value.retryable
