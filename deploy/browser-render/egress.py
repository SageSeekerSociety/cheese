"""The browser's only way out: a proxy that connects to public addresses only.

The browser runs inside the platform's network. A page it is asked to render
can redirect, or navigate from a script, or load a subresource, to an address
only that network can reach — a service on this host, another container, the
deployment's LAN, a cloud host's metadata endpoint — and the rendered result
goes back to whoever asked for the page. Checking the URL before rendering does
not see any of that, because it happens inside Chrome.

So Chrome is pointed at this proxy and every connection it makes comes through
here: HTTPS as ``CONNECT host:port``, plain HTTP as an absolute-form request.
The host is resolved, every address must be globally routable, and the
connection goes to the address that was checked, so a name cannot resolve one
way for the check and another way for the connection. A refused host gets a
403 and nothing is opened.

Playwright makes Chrome send loopback traffic through the proxy as well (it
adds ``<-loopback>`` to the bypass list), so ``localhost`` is not a way around.

If the machine has its own egress proxy (``HTTPS_PROXY``), connections are
tunnelled through it to the checked address, so the egress is kept.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

HEAD_LIMIT = 64 * 1024
HEAD_TIMEOUT = 30.0

#: Hop-by-hop headers that must not be passed on to the site.
_DROP = {b"proxy-connection", b"proxy-authorization", b"connection", b"keep-alive"}


class Refused(Exception):
    """The host is not on the public internet."""


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return [str(info[4][0]) for info in infos]


async def public_address(host: str, port: int) -> str:
    """One address ``host`` resolves to, provided every one of them is public."""
    try:
        addresses = await resolve(host, port)
    except OSError as exc:
        raise Refused(host) from exc
    if not addresses or not all(is_public(a) for a in addresses):
        raise Refused(host)
    return next((a for a in addresses if ":" not in a), addresses[0])


def _host_port(authority: str, default: int) -> tuple[str, int]:
    parts = urlsplit("//" + authority)
    if not parts.hostname:
        raise Refused(authority)
    try:
        return parts.hostname, parts.port or default
    except ValueError as exc:
        raise Refused(authority) from exc


async def _open(host: str, port: int):
    address = await public_address(host, port)
    upstream = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not upstream:
        return await asyncio.open_connection(address, port)
    proxy = urlsplit(upstream)
    reader, writer = await asyncio.open_connection(
        proxy.hostname, proxy.port or 3128
    )
    target = f"[{address}]:{port}" if ":" in address else f"{address}:{port}"
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await writer.drain()
    reply = await reader.readuntil(b"\r\n\r\n")
    if b" 200" not in reply.split(b"\r\n", 1)[0]:
        writer.close()
        raise OSError("the egress proxy refused the tunnel")
    return reader, writer


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _refuse(writer: asyncio.StreamWriter, status: bytes) -> None:
    writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    try:
        await writer.drain()
    finally:
        writer.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
        writer.close()
        return
    request_line, *header_lines = head[:-4].split(b"\r\n")
    try:
        method, target, version = request_line.decode("latin-1").split(" ", 2)
    except ValueError:
        await _refuse(writer, b"400 Bad Request")
        return

    try:
        if method == "CONNECT":
            host, port = _host_port(target, 443)
            up_reader, up_writer = await _open(host, port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            url = urlsplit(target)
            if url.scheme != "http" or not url.hostname:
                await _refuse(writer, b"400 Bad Request")
                return
            host, port = _host_port(url.netloc, 80)
            up_reader, up_writer = await _open(host, port)
            path = (url.path or "/") + (f"?{url.query}" if url.query else "")
            kept = [
                line
                for line in header_lines
                if line.split(b":", 1)[0].strip().lower() not in _DROP
            ]
            up_writer.write(
                f"{method} {path} {version}\r\n".encode("latin-1")
                + b"".join(line + b"\r\n" for line in kept)
                # One site per connection: a reused client connection must not
                # carry a request for another host down this socket.
                + b"Connection: close\r\n\r\n"
            )
            await up_writer.drain()
    except Refused:
        await _refuse(writer, b"403 Forbidden")
        return
    except OSError:
        await _refuse(writer, b"502 Bad Gateway")
        return

    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def start(host: str = "127.0.0.1", port: int = 0) -> asyncio.base_events.Server:
    """Start the proxy; ``server.sockets[0].getsockname()[1]`` is its port."""
    return await asyncio.start_server(handle, host, port, limit=HEAD_LIMIT)
