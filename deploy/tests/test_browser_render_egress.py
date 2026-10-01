#!/usr/bin/env python3
"""The render browser's proxy lets it reach the public internet and nothing else.

Chrome sends every connection through ``deploy/browser-render/egress.py``. What a
person can state about that without reading it: an internal address is refused
however it is asked for — as an HTTPS tunnel, as a plain HTTP request, by name —
and the internal service never sees a byte; a public site is reached both ways.

Loopback servers stand in for both: 127.0.0.1 plays the internal service, and
127.0.0.2 plays a public site because this test declares that one address
public and nothing else.
"""

import asyncio
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser-render"))

import egress  # noqa: E402


class Site:
    def __init__(self, host: str, body: bytes) -> None:
        self.hits = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.hits += 1
                self.send_response(200)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.httpd = ThreadingHTTPServer((host, 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


async def exchange(proxy_port: int, request: bytes, then: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(request)
    await writer.drain()
    reply = await reader.readuntil(b"\r\n\r\n")
    if then and b" 200 " in reply.split(b"\r\n", 1)[0] + b" ":
        writer.write(then)
        await writer.drain()
        while chunk := await reader.read(65536):
            reply += chunk
    writer.close()
    return reply


def status(reply: bytes) -> int:
    return int(reply.split(b" ", 2)[1])


async def main() -> None:
    internal = Site("127.0.0.1", b"INTERNAL")
    public = Site("127.0.0.2", b"PUBLIC PAGE")
    real = egress.is_public
    egress.is_public = lambda a: a == "127.0.0.2" or real(a)

    server = await egress.start()
    port = server.sockets[0].getsockname()[1]

    # Refused: a tunnel, a plain request, and a name for the same service.
    for request in (
        f"CONNECT 127.0.0.1:{internal.port} HTTP/1.1\r\n\r\n".encode(),
        f"GET http://127.0.0.1:{internal.port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode(),
        f"GET http://localhost:{internal.port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode(),
    ):
        reply = await exchange(port, request)
        assert status(reply) == 403, reply
    assert internal.hits == 0, "the internal service must not be reached"

    # A name with one public and one private address is refused as a whole.
    real_resolve = egress.resolve

    async def mixed(host: str, p: int) -> list[str]:
        if host == "mixed.test":
            return ["127.0.0.2", "10.0.0.5"]
        return await real_resolve(host, p)

    egress.resolve = mixed
    reply = await exchange(
        port, f"GET http://mixed.test:{public.port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode()
    )
    assert status(reply) == 403, reply
    egress.resolve = real_resolve

    # Reached: plain HTTP, and HTTP carried inside a tunnel.
    reply = await exchange(
        port,
        f"GET http://127.0.0.2:{public.port}/ HTTP/1.1\r\nHost: 127.0.0.2\r\n\r\n".encode(),
    )
    assert status(reply) == 200, reply
    reply = await exchange(
        port,
        f"CONNECT 127.0.0.2:{public.port} HTTP/1.1\r\n\r\n".encode(),
        then=b"GET / HTTP/1.1\r\nHost: 127.0.0.2\r\nConnection: close\r\n\r\n",
    )
    assert b"PUBLIC PAGE" in reply, reply
    assert public.hits == 2

    # Behind fake-IP DNS every name resolves to a placeholder in 198.18.0.0/15;
    # the real address is asked over HTTPS, and that is what is checked.
    async def placeholder(host: str, p: int) -> list[str]:
        if host == "site.test":
            return ["198.18.3.4"]
        return await real_resolve(host, p)

    real_over_https = egress.resolve_over_https
    egress.resolve = placeholder
    hits = public.hits

    async def points_public(host: str) -> list[str]:
        return ["127.0.0.2"]

    egress.resolve_over_https = points_public
    reply = await exchange(
        port,
        f"GET http://site.test:{public.port}/ HTTP/1.1\r\nHost: site.test\r\n\r\n".encode(),
    )
    assert status(reply) == 200, reply
    assert public.hits == hits + 1

    async def points_inward(host: str) -> list[str]:
        return ["127.0.0.1"]

    egress.resolve_over_https = points_inward
    reply = await exchange(
        port, f"CONNECT site.test:{internal.port} HTTP/1.1\r\n\r\n".encode()
    )
    assert status(reply) == 403, reply

    async def unanswerable(host: str) -> list[str]:
        raise OSError("resolver unreachable")

    egress.resolve_over_https = unanswerable
    reply = await exchange(
        port, f"CONNECT site.test:{internal.port} HTTP/1.1\r\n\r\n".encode()
    )
    assert status(reply) == 403, reply
    assert internal.hits == 0

    egress.resolve, egress.resolve_over_https = real_resolve, real_over_https
    server.close()
    print("browser-render egress: internal refused, public reached")


if __name__ == "__main__":
    asyncio.run(main())
