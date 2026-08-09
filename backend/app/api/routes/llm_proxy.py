"""Model route for machines that are not the backend's own host.

A local sandbox container reaches the pool gateway directly (it shares the
box's network), so nothing had to stand between them. A MicroCloud machine
cannot: the gateway listens on a box-local address, and handing the machine a
provider key instead would put a shared credential on hardware the platform
does not control and make spend unattributable.

So a remote screen is launched with ``ANTHROPIC_BASE_URL`` pointing here and
its own per-turn scoped cheese token as ``ANTHROPIC_AUTH_TOKEN``
(``device_provider.build_screen_launch``). This route authenticates that token,
swaps in the project's virtual gateway key — the same key its local turns run
on, so budget and attribution are unchanged — and streams the upstream response
back verbatim. The credential never leaves the box.

Anthropic-protocol-agnostic on purpose: whatever path Claude Code asks for
(``/v1/messages``, ``/v1/messages/count_tokens``, …) is forwarded as-is, so a
client-side protocol change needs no change here.
"""

import logging
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_chat_service
from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    GatewayUnavailableError,
    NotFoundError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent.chat import ChatService

logger = logging.getLogger("cheesex.llm_proxy")

# Root-mounted like the other machine-facing routers (`/sandbox`,
# `/connector`), NOT under `/api`. Everything a machine calls is built as
# ``{connector_public_base}/<backend path>``, so that base has to map 1:1 onto
# the backend's root — a deployment whose reverse proxy strips an `/api`
# prefix sets the base to `https://host/api`. Living under `/api` here would
# make this the one route that needs the prefix twice.
router = APIRouter(prefix="/llm", tags=["llm"])

# Long: a streamed model turn holds the connection open for minutes. The read
# timeout is what a slow first token hits, so it has to exceed the model's own
# time-to-first-token, not the request's total length.
_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)

# Hop-by-hop and identity-bearing headers: forwarding them would either break
# the upstream connection or leak the caller's own credential past the swap.
_DROP_REQUEST_HEADERS = frozenset(
    {
        "host",
        "authorization",
        "x-api-key",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
    }
)
_DROP_RESPONSE_HEADERS = frozenset(
    {"content-length", "connection", "keep-alive", "transfer-encoding", "upgrade"}
)


def _caller_token(request: Request) -> str:
    """The scoped token, however Claude Code chose to present it: ANTHROPIC_AUTH_TOKEN
    rides as a bearer, ANTHROPIC_API_KEY as x-api-key."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


def _upstream_url(path: str) -> str:
    base = (settings.anthropic_base_url or "").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


@router.api_route("/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def proxy(
    path: str,
    request: Request,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> StreamingResponse:
    token = _caller_token(request)
    claims = scoped_token_claims(token) if token else None
    if not claims or not claims.get("p"):
        raise AuthenticationRequiredError("A scoped cheese token is required")
    if not settings.anthropic_base_url:
        raise GatewayUnavailableError("No model pool is configured for this deployment")

    project_id = claims["p"]
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError as exc:
        raise NotFoundError("Unknown project") from exc
    key = await chat.project_gateway_key(project_uuid)
    if not key:
        # Same rule as a local turn: refuse rather than fall back to the pool's
        # own credential, which would bill every project to one bucket.
        raise GatewayUnavailableError(
            "AI gateway could not provision a project-scoped key; "
            "no model call was made"
        )

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _DROP_REQUEST_HEADERS
    }
    headers["authorization"] = f"Bearer {key}"
    headers["x-api-key"] = key
    body = await request.body()

    client = httpx.AsyncClient(timeout=_TIMEOUT)
    req = client.build_request(
        request.method,
        _upstream_url(path),
        headers=headers,
        content=body,
        params=dict(request.query_params),
    )
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("llm proxy upstream failed for project %s: %s", project_id, exc)
        raise GatewayUnavailableError("The model pool did not answer") from exc

    async def stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        headers={
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in _DROP_RESPONSE_HEADERS
        },
    )
