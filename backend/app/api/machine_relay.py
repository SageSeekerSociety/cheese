"""How the process that owns the device links answers a sandbox's request.

A request a sandbox sent on its platform socket (``device_hub.machine``) is
passed to the business backend's ``/machine`` routes, with the device and the
state directory it arrived on as the caller, vouched for by the device
connection secret. Everything the sandbox itself sent that could pose as either
is dropped first, so the only identity the backend reads is the one this
process saw. The answer is written back as it streams.

The connection owner forwards over HTTP to ``connector_origin_url``; a backend
that owns its device links itself (no ``device_connection_url``) answers in
process.
"""

from urllib.parse import unquote

import httpx

from app.core.config import settings
from app.domain.agent.device_hub.machine import (
    Handler,
    MachineRequest,
    Write,
    plain_answer,
)

#: Who the caller is, as the backend's ``/machine`` routes read it.
DEVICE_HEADER = "x-machine-device"
STATE_HEADER = "x-machine-state"
SECRET_HEADER = "x-device-connection-secret"

# Hop-by-hop, or this relay's to set.
_DROPPED = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "host",
    "content-length",
    DEVICE_HEADER,
    STATE_HEADER,
    SECRET_HEADER,
}


def _under_machine(target: str) -> str | None:
    """``/machine`` plus the sandbox's path, or None for a path that would
    leave it. The secret goes with the request, so a path an HTTP client or a
    proxy would resolve elsewhere (``..``, encoded or not, a backslash) is
    refused rather than cleaned up."""
    path, _, query = target.partition("?")
    decoded = unquote(path)
    if not path.startswith("/") or "\\" in decoded or "\x00" in decoded:
        return None
    if any(part in (".", "..") for part in decoded.split("/")):
        return None
    return "/machine" + path + (f"?{query}" if query else "")


def forwarder(client: httpx.AsyncClient, base: str) -> Handler:
    """Answer each machine request from the backend at ``base`` over ``client``."""

    async def answer(request: MachineRequest, write: Write) -> None:
        headers = [(k, v) for k, v in request.headers if k.lower() not in _DROPPED]
        headers += [
            (DEVICE_HEADER, request.device_id),
            (STATE_HEADER, request.state),
            (SECRET_HEADER, settings.device_connection_auth_secret),
        ]
        target = _under_machine(request.target)
        if target is None:
            await write(plain_answer(400, "not a path the platform serves"))
            return
        async with client.stream(
            request.method,
            f"{base.rstrip('/')}{target}",
            headers=headers,
            content=request.body if request.chunks is None else request.stream(),
            timeout=httpx.Timeout(30, read=None),
        ) as response:
            head = [f"HTTP/1.1 {response.status_code} {response.reason_phrase}"]
            head += [
                f"{k}: {v}"
                for k, v in response.headers.multi_items()
                if k.lower() not in _DROPPED
            ]
            # The answer ends where the connection does.
            head.append("connection: close")
            await write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
            async for chunk in response.aiter_raw():
                await write(chunk)

    return answer


def over_http(origin: str) -> Handler:
    # An address inside the deployment: never through an outbound proxy the
    # environment may name, which would also be handed the secret.
    return forwarder(httpx.AsyncClient(trust_env=False), origin)


def in_process(app) -> Handler:
    # An ASGI transport holds the whole answer before handing it over; only a
    # backend that owns its own device links, a single-process deployment,
    # goes this way.
    return forwarder(
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app)), "http://backend"
    )
