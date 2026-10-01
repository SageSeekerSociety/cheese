"""Only the public internet: which addresses a fetch may reach.

The backend reads pages from inside the platform's own network, where it can
see what the internet cannot: services on the same host, the other containers,
the LAN the deployment sits on, a cloud host's metadata endpoint. A fetch that
took any URL it was handed would read those for whoever asked and return them.

So every connection a fetch makes goes to an address checked here first: the
host is resolved, and every address it resolves to must be globally routable.
Which destinations are allowed is decided in ``addresses.py``, shared with the
egress proxy private chats reach the network through. Resolving, rather than
matching the text of the URL, is what makes ``127.1``, ``0x7f000001``,
``localhost`` and a public name pointing at ``10.0.0.5`` the same case.

A check alone would leave a gap between checking a name and connecting to it —
the name can resolve differently the second time. The HTTP client below closes
it by connecting to the very address that was checked, at the point where every
connection is opened, redirect hops included.

Some machines resolve every name to a placeholder in ``198.18.0.0/15`` and let
a transparent proxy connect to the real host by name ("fake-IP" DNS). Those
placeholders say nothing about where a connection really goes, so when every
address a name resolves to is one of them, the name is asked again over DNS over
HTTPS (``settings.fetch_dns_over_https``), the real addresses are checked the
same way, and the connection goes to the real address — the proxy carries a
connection to a real IP as readily as one to a placeholder. If that answer
cannot be had, the name is refused.

A refused address stops the whole fetch. The ladder in ``service.fetch`` treats
any rung's failure as "try the next one", and the next ones hand the URL to a
third party or a browser; ``NotPublic`` is therefore raised past every rung, not
reported as a miss.
"""

from __future__ import annotations

import asyncio
import socket
from urllib.parse import urlsplit

import httpcore
import httpx

from app.core.config import settings
from app.domain.fetch.addresses import (
    NotPublic,
    needs_real_addresses,
    over_https_answers,
    vetted,
)

__all__ = ["MAX_REDIRECTS", "NotPublic", "check", "client", "public_address"]

#: A redirect chain longer than this is not a page, it is a loop or a probe.
MAX_REDIRECTS = 5


async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def _resolve_over_https(host: str) -> list[str]:
    """``host``'s IPv4 addresses from a DNS-over-HTTPS resolver's JSON API."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), trust_env=False) as c:
        r = await c.get(
            settings.fetch_dns_over_https,
            params={"name": host, "type": "A"},
            headers={"accept": "application/dns-json"},
        )
    r.raise_for_status()
    return over_https_answers(r.json())


async def public_address(host: str, port: int) -> str:
    """One address ``host`` resolves to, provided ALL of them are public."""
    try:
        addresses = await _resolve(host, port)
    except OSError as exc:
        raise NotPublic(f"{host} does not resolve") from exc
    if needs_real_addresses(addresses):
        try:
            addresses = await _resolve_over_https(host)
        except Exception as exc:  # noqa: BLE001 — unknown means refused
            raise NotPublic(f"{host} could not be resolved to a real address") from exc
    return vetted(host, addresses)[0]


def _target(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise NotPublic(f"only http and https can be fetched, not {parts.scheme!r}")
    if not parts.hostname:
        raise NotPublic("the URL names no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise NotPublic("the URL has an invalid port") from exc
    return parts.hostname, port


async def check(url: str) -> str:
    """Refuse ``url`` unless it is http(s) on a public host; return the address
    the connection must go to."""
    host, port = _target(url)
    return await public_address(host, port)


class _PublicOnly(httpcore.AsyncNetworkBackend):
    """Opens TCP connections to checked addresses only.

    Sits where the HTTP stack turns a host name into a socket, so it sees every
    connection: the first request, every redirect hop, every new origin. The
    URL and the Host header keep the name; only the socket goes to the address
    that was checked.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend) -> None:
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        address = await public_address(host, port)
        return await self._inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise NotPublic("a fetch cannot open a local socket")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PublicTransport(httpx.AsyncHTTPTransport):
    def __init__(self) -> None:
        super().__init__()
        # httpx does not take a network backend, but its connection pool does.
        # The loopback tests in test_fetch_guard.py fail if this stops taking
        # effect after an upgrade.
        pool = self._pool
        pool._network_backend = _PublicOnly(pool._network_backend)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        _target(str(request.url))
        return await super().handle_async_request(request)


def client(**kwargs) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` that reaches public addresses only.

    ``trust_env`` is off: an egress proxy from the environment would be mounted
    as its own transport and carry requests past this one. No deployment sets
    one for the backend; if one ever needs to, it has to be wired in here.
    """
    return httpx.AsyncClient(
        transport=_PublicTransport(),
        trust_env=False,
        max_redirects=MAX_REDIRECTS,
        **kwargs,
    )
