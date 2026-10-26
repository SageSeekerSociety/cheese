"""The forge CLIs' launcher on a machine with a platform socket.

The rules, as a person would state them: the forge credentials are asked of
the socket, with no token; a forge tunnel connection is opened on a credential
the socket hands out for it.
"""

import json
import os
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from app.domain.agent import forge_cli


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("socket", 0)


@pytest.fixture
def platform_socket(monkeypatch):
    asked = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append((self.path, self.headers.get("x-cheese-token")))
            payload = {
                "/api/sandbox/forge-token": {"data": {"kind": "github_app"}},
                "/credential": {"token": "handed", "expires_in": 60},
            }[self.path]
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "platform.sock")
    server = _Server(path, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("CHEESE_PLATFORM_SOCKET", path)
    monkeypatch.setenv("CHEESE_TOKEN", "the-machines-own")
    yield asked
    server.shutdown()
    server.server_close()


def test_the_forge_credentials_are_asked_of_the_socket_without_a_token(
    platform_socket,
):
    assert forge_cli.credentials() == {"kind": "github_app"}
    assert platform_socket == [("/api/sandbox/forge-token", None)]


def test_a_tunnel_connection_is_opened_on_a_handed_credential(platform_socket):
    assert forge_cli._tunnel_secret() == "handed"
