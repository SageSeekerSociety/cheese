"""A byte pipe from a remote machine to the metering proxy, over the one path
that already works.

A machine's subscription turn has to reach the meter's CONNECT listener, and it
has exactly one way to be steered there: ``HTTPS_PROXY``. It cannot use the
container trick (rewriting /etc/hosts needs root it does not have), and it
cannot be pointed at an HTTP endpoint instead — setting ``ANTHROPIC_BASE_URL``
flips Claude Code into API-key mode, where it ignores the OAuth token outright
and no subscription turn is possible at all.

So the machine needs a TCP path to the listener, and measured 2026-08-14 it does
not have one: packets to the box's 8444 are dropped before its NIC (nothing in
`tcpdump`, while :22 flows normally), by a filter at the hypervisor's bridge
layer that the box itself cannot see or change.

What the machine DOES have is the path it already uses for everything else:

    machine ─► gateway:443 (APISIX) ─► app:8081 (this backend) ─► the meter

Every hop there is in production use today — the connector rides it — and the
gateway's routes already carry ``enable_websocket``. An L7 proxy cannot forward
HTTP CONNECT, but it forwards a WebSocket, so that is the shape: the machine
speaks CONNECT to a helper on its own loopback, the helper wraps each connection
in a WebSocket to here, and this hands the bytes to the listener unchanged.

The pipe is deliberately dumb. It does NOT parse or answer CONNECT — the meter
still performs its own handshake, its own scoped-token check, and its own
Anthropic-only host gate, exactly as it does for a caller that reached it
directly. Two consequences worth stating: the meter's rules cannot be weakened
by adding a transport, and this endpoint cannot become a way to reach anything
else on the box, because the only address it ever dials is the configured
listener.
"""

import asyncio
import logging

from fastapi import APIRouter, Header, Query, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.sandbox_auth import scoped_token_claims

logger = logging.getLogger("cheesex.llm_tunnel")

# Same root-mounted prefix as the other machine-facing routers. Sharing it with
# `llm_proxy`'s catch-all is safe and deliberate: Starlette matches a WebSocket
# scope only against WebSocket routes, so an HTTP `/{path:path}` never sees this
# handshake regardless of which module registers first.
router = APIRouter(prefix="/llm", tags=["llm"])

# One read; the pipe is latency-sensitive and a turn's frames are small.
_CHUNK = 65536

# How long to wait for the listener to answer. Short: it is a process on this
# same box, so anything slow means it is down, and a caller left hanging cannot
# tell that from a stalled model.
_CONNECT_TIMEOUT_S = 5.0


def _listener() -> tuple[str, int]:
    return settings.subscription_proxy_host, settings.subscription_proxy_connect_port


async def _pump_ws_to_tcp(ws: WebSocket, writer: asyncio.StreamWriter) -> None:
    while True:
        data = await ws.receive_bytes()
        writer.write(data)
        await writer.drain()


async def _pump_tcp_to_ws(reader: asyncio.StreamReader, ws: WebSocket) -> None:
    while True:
        data = await reader.read(_CHUNK)
        if not data:
            return  # listener closed; ending this side ends the pair
        await ws.send_bytes(data)


@router.websocket("/tunnel")
async def tunnel(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
) -> None:
    """Relay one TCP connection to the metering proxy's CONNECT listener.

    Authenticated with the same scoped cheese token the caller would have used
    as the proxy password had it reached the listener directly, so opening this
    transport widens nothing: a caller that cannot prove which project to bill
    is refused here exactly as it would be there. Verification is an HMAC over
    the token's own claims, so this route touches no database — which also keeps
    it clear of the trap a long-lived socket falls into when it holds a request
    session open for the life of the connection (#356).
    """
    claims = scoped_token_claims(x_cheese_session or token or "")
    if not claims or not claims.get("p"):
        # Refused BEFORE accept: an unaccepted handshake is a plain HTTP 403 the
        # helper can report as "bad token", instead of a socket that opens and
        # then dies for reasons it cannot distinguish from a network fault.
        await websocket.close(
            code=1008, reason="a valid scoped cheese token is required"
        )
        return

    host, port = _listener()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=_CONNECT_TIMEOUT_S
        )
    except (OSError, TimeoutError) as exc:
        logger.warning(
            "llm tunnel could not reach the meter at %s:%s: %s", host, port, exc
        )
        await websocket.close(code=1011, reason="the metering proxy did not answer")
        return

    await websocket.accept()
    to_tcp = asyncio.create_task(_pump_ws_to_tcp(websocket, writer))
    to_ws = asyncio.create_task(_pump_tcp_to_ws(reader, websocket))
    try:
        # Either direction ending ends the connection: a half-open pipe would
        # leave the caller waiting on a response that can no longer arrive.
        await asyncio.wait({to_tcp, to_ws}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in (to_tcp, to_ws):
            task.cancel()
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
