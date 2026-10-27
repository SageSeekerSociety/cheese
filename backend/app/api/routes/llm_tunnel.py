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

The two pumps and the activity clock they share with `routes/forge_token.py`
live in `app.api.ws_pipe`; what stays here is this route's own policy — which
listener it dials, and how long it may stay quiet.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.ws_pipe import _Activity, _pump_tcp_to_ws, _pump_ws_to_tcp
from app.core import credentials
from app.core.config import settings
from app.core.sandbox_auth import held_by_a_machine, scoped_token_claims

logger = logging.getLogger("cheesex.llm_tunnel")

# Same root-mounted prefix as the other machine-facing routers. Sharing it with
# `llm_proxy`'s catch-all is safe and deliberate: Starlette matches a WebSocket
# scope only against WebSocket routes, so an HTTP `/{path:path}` never sees this
# handshake regardless of which module registers first.
router = APIRouter(prefix="/llm", tags=["llm"])

# How long to wait for the listener to answer. Short: it is a process on this
# same box, so anything slow means it is down, and a caller left hanging cannot
# tell that from a stalled model.
_CONNECT_TIMEOUT_S = 5.0


# How long a pipe may carry nothing, in either direction, before it is cut. A
# live model stream is never this quiet — it sends deltas and periodic pings —
# so silence this long means a hop underneath died without saying so: the meter
# recreated by a deploy left an in-flight turn hanging for minutes with every
# hop still ESTABLISHED (2026-09-25). Closing is what lets the caller retry.
IDLE_TIMEOUT_S = 180.0
_IDLE_CHECK_S = 5.0


def _listener() -> tuple[str, int]:
    return settings.subscription_proxy_host, settings.subscription_proxy_connect_port


async def _until_idle(activity: _Activity, idle_s: float, check_s: float) -> None:
    while True:
        remaining = idle_s - (time.monotonic() - activity.last)
        if remaining <= 0:
            return
        await asyncio.sleep(min(check_s, remaining))


@router.websocket("/tunnel")
async def tunnel(
    websocket: WebSocket,
) -> None:
    """Relay one TCP connection to the metering proxy's CONNECT listener.

    Authenticated with the same scoped cheese token the caller would have used
    as the proxy password had it reached the listener directly, so opening this
    transport widens nothing: a caller that cannot prove which project to bill
    is refused here exactly as it would be there. Verification is a signature
    over the token's own claims, so this route touches no database — which also keeps
    it clear of the trap a long-lived socket falls into when it holds a request
    session open for the life of the connection (#356).
    """
    claims = scoped_token_claims(credentials.presented(websocket.headers))
    # A sandbox's own credential is no tunnel credential: the tunnel carries a
    # session host's Claude Code to the metering proxy, and a sandbox asks for
    # a model on its platform socket (`sandbox_auth.held_by_a_machine`).
    if not claims or not claims.get("p") or held_by_a_machine(claims):
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
    activity = _Activity()
    to_tcp = asyncio.create_task(_pump_ws_to_tcp(websocket, writer, activity))
    to_ws = asyncio.create_task(_pump_tcp_to_ws(reader, websocket, activity))
    idle = asyncio.create_task(_until_idle(activity, IDLE_TIMEOUT_S, _IDLE_CHECK_S))
    try:
        # Either direction ending ends the connection: a half-open pipe would
        # leave the caller waiting on a response that can no longer arrive.
        await asyncio.wait({to_tcp, to_ws, idle}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in (to_tcp, to_ws, idle):
            task.cancel()
        if idle.done() and not idle.cancelled():
            logger.warning(
                "llm tunnel carried nothing for %.0fs; closing it so the caller "
                "retries",
                IDLE_TIMEOUT_S,
            )
            try:
                await websocket.close(code=1011, reason="the tunnel went silent")
            except RuntimeError:
                pass
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
