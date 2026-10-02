"""HTTP clients for the forge's REST API, sharing one pool of connections.

Every call to GitHub or Forgejo builds its own `httpx.AsyncClient`, and a client
closes its connections when it is done. Against GitHub that means every call
pays a fresh TLS handshake, which from the deployment costs 1-2.5 s against
roughly 0.3 s for the answer itself; a read that takes four calls spent most of
its nine seconds on handshakes.

`forge_client` keeps the per-call client — its timeout, auth, headers and
cookies stay exactly the call site's — and gives it a transport that borrows
connections from one pool kept open for the life of the application
(`reuse_forge_connections`, entered by the lifespan). Outside that, and on any
other event loop than the one the pool was opened on, a client is built exactly
as before. A caller that passes its own transport (a test's fake forge) keeps
it.

Every client also reads the quota GitHub reports on each answer into
`forge_quota`, so that what one call learned (the installation is out of quota
until a given time) reaches the next caller before it spends a request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core import forge_quota

#: GitHub closes a connection that has sat idle somewhere between 20 and 60
#: seconds (measured from the deployment). Letting go of it first means a call
#: never sends on a socket the other end is closing.
KEEPALIVE_S = 15.0


class _Borrowed(httpx.AsyncBaseTransport):
    """Sends through the shared pool; closing a client that holds it leaves
    the pool open."""

    def __init__(self, pool: httpx.AsyncClient) -> None:
        self._pool = pool

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # The pool's own routing, so environment proxies apply as they would to
        # a client built without a transport.
        transport = self._pool._transport_for_url(request.url)
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        return None


_shared: tuple[asyncio.AbstractEventLoop, _Borrowed] | None = None


@asynccontextmanager
async def reuse_forge_connections() -> AsyncIterator[None]:
    """Keep forge connections open until the application stops."""
    global _shared
    previous = _shared
    pool = httpx.AsyncClient(limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S))
    _shared = (asyncio.get_running_loop(), _Borrowed(pool))
    try:
        yield
    finally:
        _shared = previous
        await pool.aclose()


def forge_client(
    transport: httpx.AsyncBaseTransport | None = None, **kwargs: Any
) -> httpx.AsyncClient:
    """An `httpx.AsyncClient` for one forge call, on the shared pool when one
    is open on this event loop."""
    if transport is None and _shared is not None:
        loop, borrowed = _shared
        if loop is asyncio.get_running_loop():
            transport = borrowed
    if transport is not None:
        kwargs["transport"] = transport
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["response"] = [*hooks.get("response", []), forge_quota.observe]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)
