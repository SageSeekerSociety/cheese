"""Where a private chat's commands can get to through the egress proxy.

The rules a person could state: the public internet is reachable over HTTP,
HTTPS (``CONNECT``) and SOCKS5, by its real address even where the host's
resolver hands out fake-IP placeholders; nothing internal is, whether named by
address or by a name that resolves inward; the platform endpoint is the one
internal exception, at exactly its ``host:port``; and only the private chats'
own network may use the proxy at all.

The proxy is spoken to over real sockets on loopback. As in
``test_fetch_guard.py``, 127.0.0.2 plays a public site and 127.0.0.1 the inside
of the platform; the host's resolver (``socket.getaddrinfo``) is stood in for,
and the DNS-over-HTTPS resolver is a real HTTP server.
"""

import http.client
import ipaddress
import json
import socket
import struct
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from app.domain.agent.harness.claude_code.remote_execution import private_egress
from app.domain.fetch import addresses

#: What the host's resolver says, fake-IP style.
SYSTEM = {
    "site.test": ["198.18.0.155"],
    "db.test": ["198.18.2.194"],
    "intranet.test": ["127.0.0.1"],
    "split.test": ["127.0.0.2", "127.0.0.1"],
}
#: What DNS over HTTPS says: the real addresses.
REAL = {"site.test": ["127.0.0.2"], "db.test": ["127.0.0.1"]}


class _Server:
    def __init__(self, host: str, body: str) -> None:
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                data = body.encode()
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._httpd = HTTPServer((host, 0), Handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()


@pytest.fixture
def site() -> Iterator[_Server]:
    server = _Server("127.0.0.2", "PUBLIC")
    yield server
    server.close()


@pytest.fixture
def internal() -> Iterator[_Server]:
    server = _Server("127.0.0.1", "INTERNAL")
    yield server
    server.close()


@pytest.fixture
def platform() -> Iterator[_Server]:
    server = _Server("127.0.0.1", "PLATFORM")
    yield server
    server.close()


@pytest.fixture
def world(monkeypatch):
    """127.0.0.2 is public; the host's resolver answers as in ``SYSTEM``."""
    real_public = addresses.is_public
    monkeypatch.setattr(
        addresses, "is_public", lambda a: a == "127.0.0.2" or real_public(a)
    )
    real_resolve = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host in SYSTEM:
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 0))
                for a in SYSTEM[host]
            ]
        return real_resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture
def over_https() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = parse_qs(urlsplit(self.path).query)["name"][0]
            body = json.dumps(
                {"Answer": [{"type": 1, "data": a} for a in REAL.get(name, [])]}
            ).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/resolve"
    server.shutdown()


def _start(policy, clients=("127.0.0.0/8",)) -> tuple[str, int]:
    listener = socket.create_server(("127.0.0.1", 0))
    networks = [ipaddress.ip_network(c) for c in clients]

    def run():
        try:
            private_egress.serve(listener, policy, networks)
        except OSError:
            pass

    threading.Thread(target=run, daemon=True).start()
    return listener.getsockname()


@pytest.fixture
def proxy(world, over_https, platform) -> tuple[str, int]:
    return _start(private_egress.Policy(over_https, ("127.0.0.1", platform.port)))


def via_connect(proxy, host, port) -> tuple[int, str]:
    """An HTTPS-style tunnel, with plain HTTP inside it."""
    conn = http.client.HTTPConnection(*proxy, timeout=10)
    conn.set_tunnel(host, port)
    try:
        conn.request("GET", "/through-tunnel")
        response = conn.getresponse()
        return response.status, response.read().decode()
    except OSError:  # the tunnel was refused
        return 0, ""
    finally:
        conn.close()


def via_http(proxy, url) -> tuple[int, str]:
    conn = http.client.HTTPConnection(*proxy, timeout=10)
    try:
        conn.request("GET", url)
        response = conn.getresponse()
        return response.status, response.read().decode()
    finally:
        conn.close()


def via_socks(proxy, host, port) -> tuple[int, str]:
    """SOCKS5 with the name sent to the proxy (``socks5h``); the reply code and
    what an HTTP GET through the tunnel returned."""
    with socket.create_connection(proxy, timeout=10) as sock:
        sock.sendall(b"\x05\x01\x00")
        assert sock.recv(2) == b"\x05\x00"
        name = host.encode()
        sock.sendall(
            b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port)
        )
        reply = sock.recv(10)
        if reply[1] != 0:
            return reply[1], ""
        sock.sendall(f"GET / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
        data = b""
        while chunk := sock.recv(4096):
            data += chunk
        return 0, data.decode().rsplit("\r\n\r\n", 1)[-1]


def test_a_public_name_behind_a_placeholder_is_reached_by_its_real_address(proxy, site):
    assert via_connect(proxy, "site.test", site.port) == (200, "PUBLIC")
    assert via_socks(proxy, "site.test", site.port) == (0, "PUBLIC")
    assert via_http(proxy, f"http://site.test:{site.port}/") == (200, "PUBLIC")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # an internal address
        "localhost",
        "intranet.test",  # a name that resolves inward
        "db.test",  # internal behind a placeholder
        "split.test",  # one public and one internal record
        "169.254.169.254",
    ],
)
def test_nothing_internal_is_reached(proxy, internal, host):
    port = internal.port
    status, body = via_connect(proxy, host, port)
    assert status != 200 and "INTERNAL" not in body
    code, body = via_socks(proxy, host, port)
    assert code != 0 and "INTERNAL" not in body
    status, body = via_http(proxy, f"http://{host}:{port}/")
    assert status == 403 and "INTERNAL" not in body
    assert internal.hits == []


def test_the_platform_is_reached_at_exactly_its_host_and_port(
    proxy, platform, internal
):
    assert via_http(proxy, f"http://127.0.0.1:{platform.port}/api") == (
        200,
        "PLATFORM",
    )
    assert via_socks(proxy, "127.0.0.1", platform.port) == (0, "PLATFORM")
    # The same host on another port is just another internal service.
    assert via_http(proxy, f"http://127.0.0.1:{internal.port}/")[0] == 403
    assert internal.hits == []


def test_only_the_private_chats_network_may_use_the_proxy(world, over_https, site):
    elsewhere = _start(private_egress.Policy(over_https), clients=("10.0.0.0/8",))
    with pytest.raises((OSError, http.client.HTTPException)):
        via_http(elsewhere, f"http://site.test:{site.port}/")
    assert site.hits == []
