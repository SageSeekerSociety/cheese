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
until a given time) reaches the next caller before it spends a request, and
asks its plain reads conditionally (`forge_etags`), so an unchanged answer
costs no quota.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

import httpx

from app.core import forge_etags, forge_quota, metrics

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

#: The `job` label's two values. A label carrying the job's name would grow
#: with the job list and answer a question the counters are not for: whether a
#: request was made by the platform's own clock or by somebody else.
JOB_OTHER = "other"
JOB_PERIODIC = "job"

_job: ContextVar[str] = ContextVar("forge_request_job", default=JOB_OTHER)


@contextmanager
def background_job() -> Iterator[None]:
    """Attribute forge requests made inside to a periodic job's own reads.

    Sync on purpose: ``PeriodicRunner`` wraps only the awaited job call, so the
    context reaches the task's async sends without changing the scheduler. It
    is reset in ``finally`` even when the job raises.
    """
    token = _job.set(JOB_PERIODIC)
    try:
        yield
    finally:
        _job.reset(token)


def _response_bucket(request: httpx.Request, status_code: int) -> str:
    """Which response counter one raw answer belongs to.

    The ETag layer turns a 304 into a cached 200 *after* the transport answers,
    so this reads the original status. ``GET /rate_limit`` is free and search
    spends a quota other than core, so neither is a core REST charge; nothing
    here is an exact bill, only a count by resource and original status.
    """
    path = request.url.path.rstrip("/")
    if path.endswith("/graphql"):
        return "graphql"
    if path.endswith("/rate_limit"):
        return "free_rate_limit"
    if status_code == 304:
        return "core_rest_304"
    if "/search/" in path:
        return "other"
    return "core_rest_non_304"


async def _count_request(request: httpx.Request) -> None:
    """Count one attempt that is about to be sent, when it is attributable.

    Only requests carrying a token the platform minted for an installation are
    counted; token minting (App JWT), Forgejo and anonymous calls are not.
    The installation and job ride on the request so the response hook needs no
    second owner lookup.
    """
    installation = forge_quota.installation_of(request)
    if installation is None:
        return
    job = _job.get()
    request.extensions["forge_installation"] = installation
    request.extensions["forge_job"] = job
    metrics.registry.counter(
        "forge_requests_started_total",
        {"job": job, "installation": str(installation)},
    ).inc()


async def _count_response(response: httpx.Response) -> None:
    """Count one raw response in its resource/status bucket.

    Called from the client's hooks, before ``forge_etags`` replaces a 304 with
    the cached 200. Started minus the response buckets is requests that never
    answered (or are still in flight), not a count of charges.
    """
    request = response.request
    installation = request.extensions.get("forge_installation")
    job = request.extensions.get("forge_job")
    if installation is None or job is None:
        return
    bucket = _response_bucket(request, response.status_code)
    metrics.registry.counter(
        f"forge_responses_{bucket}_total",
        {"job": job, "installation": str(installation)},
    ).inc()


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
    # Callers' request hooks stay first: a hook that refuses before sending
    # (the CI reader yields on low quota) must not be counted as an attempt.
    # Response hooks run inside the transport call, before `forge_etags` can
    # turn a raw 304 into a cached 200.
    hooks["request"] = [*hooks.get("request", []), _count_request]
    hooks["response"] = [
        *hooks.get("response", []),
        forge_quota.observe,
        _count_response,
    ]
    client = httpx.AsyncClient(event_hooks=hooks, **kwargs)
    plain = client.send

    async def send(
        request: httpx.Request, *, stream: bool = False, **options: Any
    ) -> httpx.Response:
        # Reads that GitHub can answer with 304 are asked conditionally;
        # streamed reads and writes go out as they are.
        if stream or request.method != "GET":
            return await plain(request, stream=stream, **options)
        return await forge_etags.send(plain, request, **options)

    client.send = send  # type: ignore[method-assign]
    return client
