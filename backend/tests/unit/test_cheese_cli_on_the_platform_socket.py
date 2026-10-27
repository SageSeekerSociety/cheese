"""The machine's `cheese` asks the platform on its platform socket.

The rules, as a person would state them:

- with a platform socket to dial, every platform call goes down it, under
  `/api`, without the token, and whatever proxy the environment names is not
  used for it;
- with no credential to name the session, the socket names it;
- the process that loads the CLI keeps its own urllib;
- a body too large for the socket goes straight to the platform, on a
  credential the socket hands out for it rather than the machine's token.

The socket and the platform are loopback fixtures; the shipped CLI runs as it is.
"""

import importlib.util
import json
import os
import socketserver
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


def _recording(seen):
    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length) if length else b""
            seen.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "token": self.headers.get("authorization"),
                    "bytes": len(body),
                }
            )
            payload = {
                "/whoami": {"session": "the-session"},
                "/credential": {"token": "handed-for-a-minute", "expires_in": 60},
            }.get(self.path, {"data": {}})
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_PUT = _answer

        def log_message(self, *args):
            pass

    return Handler


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        # BaseHTTPRequestHandler logs the peer as a pair.
        return request, ("socket", 0)


@pytest.fixture
def machine(monkeypatch):
    on_socket, direct = [], []
    folder = tempfile.mkdtemp(dir="/tmp")
    path = os.path.join(folder, "platform.sock")
    socket_server = _UnixHTTPServer(path, _recording(on_socket))
    platform = ThreadingHTTPServer(("127.0.0.1", 0), _recording(direct))
    for server in (socket_server, platform):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("CHEESE_PLATFORM_SOCKET", path)
    monkeypatch.setenv("CHEESE_API", f"http://127.0.0.1:{platform.server_port}")
    monkeypatch.setenv("CHEESE_TOKEN", "the-token")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    # The platform itself is reached as the environment says: here, not proxied.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    before = urllib.request._opener
    source = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
    loader = SourceFileLoader("cheese_on_the_socket", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    module.opener_before_loading = before
    yield module, on_socket, direct
    for server in (socket_server, platform):
        server.shutdown()
        server.server_close()


def test_a_platform_call_goes_down_the_socket_without_the_token(machine):
    cheese, on_socket, direct = machine
    cheese._call("GET", "/topics/t")
    assert on_socket == [
        {"method": "GET", "path": "/api/topics/t", "token": None, "bytes": 0}
    ]
    assert direct == []


def test_a_machine_session_is_named_by_the_socket(machine):
    cheese, on_socket, _ = machine
    assert cheese._own_session(os.environ) == "the-session"
    assert on_socket[-1]["path"] == "/whoami"


def test_a_session_on_the_machine_asks_down_the_socket_too(machine):
    cheese, on_socket, _ = machine
    cheese.LocalHost().request({"method": "GET", "path": "/projects/p"})
    assert on_socket[-1]["path"] == "/api/projects/p"
    assert on_socket[-1]["token"] is None


def test_a_large_body_goes_down_the_socket_without_a_credential(
    machine,
):
    cheese, on_socket, direct = machine
    large = "x" * (30 * 1024 * 1024 + 1)
    cheese._call("POST", "/topics/t/files", {"content": large})
    assert [call["path"] for call in on_socket] == ["/api/topics/t/files"]
    assert on_socket[-1]["token"] is None
    assert on_socket[-1]["bytes"] > 30 * 1024 * 1024
    assert direct == []


def test_gits_relay_password_is_a_handed_credential(machine):
    cheese, _, _ = machine
    assert cheese._handed_token() == "handed-for-a-minute"


def test_loading_the_cli_leaves_the_processs_urllib_as_it_was(machine):
    cheese, _, _ = machine
    assert urllib.request._opener is cheese.opener_before_loading
