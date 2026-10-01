"""What a private chat's container learns when it looks a name up.

Every DNS query from the container is answered by its network gate. The rules a
person could state: a name on the internet resolves to its real address even
where the host's resolver hands out fake-IP placeholders; a name with an
internal address does not resolve at all; the platform's own host resolves to
the address the container is allowed to reach it at.

The gate is asked over a real UDP socket. The host's resolver is the one thing
stood in for (``socket.getaddrinfo``), and the DNS-over-HTTPS resolver is a real
HTTP server on loopback.
"""

import contextlib
import json
import socket
import struct
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from app.domain.agent.harness.claude_code.remote_execution import private_network

#: What the host's resolver says, fake-IP style: placeholders for everything.
SYSTEM = {
    "pypi.org": ["198.18.0.155"],
    "cheese-dev-env1-postgresql": ["198.18.2.194"],
    "intranet.example": ["192.168.16.7"],
    "split.example": ["151.101.0.223", "10.0.0.5"],
    "direct.example": ["151.101.64.223"],
}
#: What DNS over HTTPS says: the real addresses.
REAL = {
    "pypi.org": ["151.101.0.223", "151.101.64.223"],
    "cheese-dev-env1-postgresql": ["192.168.16.7"],
}


@pytest.fixture
def host_resolver(monkeypatch):
    real = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host in SYSTEM:
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 0))
                for a in SYSTEM[host]
            ]
        if host.endswith((".example", ".invalid")):
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        return real(host, port, *args, **kwargs)

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


@pytest.fixture
def gate(host_resolver, over_https) -> Iterator[tuple[str, int]]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    platform = {"host.docker.internal": ["172.17.0.1"]}

    def run():
        with contextlib.suppress(OSError):  # the socket closing at teardown
            private_network.serve(
                sock,
                lambda name: private_network.public_addresses(
                    name, over_https, platform=platform
                ),
            )

    threading.Thread(target=run, daemon=True).start()
    yield sock.getsockname()
    sock.close()


def ask(server, name, qtype=1):
    query = struct.pack(">HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    query += b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    query += b"\x00" + struct.pack(">HH", qtype, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(10)
        client.sendto(query, server)
        reply = client.recv(4096)
    ident, flags, _, count = struct.unpack(">HHHH", reply[:8])
    assert ident == 0x1234
    offset = len(query)
    addresses = []
    for _ in range(count):
        _, rtype, _, _, length = struct.unpack(">HHHIH", reply[offset : offset + 12])
        if rtype == 1:
            addresses.append(socket.inet_ntoa(reply[offset + 12 : offset + 16]))
        offset += 12 + length
    return flags & 0x000F, sorted(addresses)


def test_a_name_behind_a_placeholder_resolves_to_its_real_address(gate):
    assert ask(gate, "pypi.org") == (0, ["151.101.0.223", "151.101.64.223"])


def test_a_name_that_resolves_directly_keeps_its_address(gate):
    assert ask(gate, "direct.example") == (0, ["151.101.64.223"])


@pytest.mark.parametrize(
    "name",
    [
        "cheese-dev-env1-postgresql",  # internal behind a placeholder
        "intranet.example",  # internal outright
        "split.example",  # one public and one internal record
        "nowhere.invalid",
    ],
)
def test_a_name_with_an_internal_address_does_not_resolve(gate, name):
    rcode, addresses = ask(gate, name)
    assert addresses == []
    assert rcode != 0


def test_the_platform_host_resolves_to_where_the_container_may_reach_it(gate):
    assert ask(gate, "host.docker.internal") == (0, ["172.17.0.1"])


def test_no_name_resolves_to_an_ipv6_address(gate):
    assert ask(gate, "pypi.org", qtype=28) == (0, [])
