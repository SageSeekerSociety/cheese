"""Model route for machines that are not the backend's own host.

A local sandbox container reaches the pool gateway directly (it shares the
box's network), so nothing had to stand between them. A MicroCloud machine
cannot: the gateway listens on a box-local address, and handing the machine a
provider key instead would put a shared credential on hardware the platform
does not control and make spend unattributable.

So a remote screen is launched with ``ANTHROPIC_BASE_URL`` pointing here and
its own per-turn scoped cheese token as ``ANTHROPIC_AUTH_TOKEN``
(``machine_launcher.screen_env``). This route authenticates that token,
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
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service, get_db
from app.api.response import ok
from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    GatewayUnavailableError,
    NotFoundError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent.budget_proxy import BudgetState, decide
from app.domain.agent.chat import ChatService
from app.domain.agent.supply import GATEWAY, SUBSCRIPTION, resolve_pool
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.usage.repositories import ComputeGrantRepository

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


# Registered BEFORE the catch-all below — FastAPI matches in declaration order,
# and the catch-all would otherwise swallow this path and forward it upstream.
@router.post("/admission", include_in_schema=False)
async def admission(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """Whether the caller's project can afford one more subscription turn.

    The metering proxy calls this BEFORE forwarding a ``/v1/messages`` request;
    the Bearer is the sandbox's per-session scoped cheese token (#198), so the
    project comes from verified claims rather than a spoofable header. The
    decision reads the same compute-grant balance the gateway brake prices in
    USD — one budget, two enforcement points. Refusing is this endpoint's only
    job: the proxy fails OPEN on transport errors (a broken brake must not be
    a broken platform) and keeps its rolling token cap as the backstop.
    """
    token = _caller_token(request)
    claims = scoped_token_claims(token) if token else None
    if not claims or not claims.get("p"):
        raise AuthenticationRequiredError("A scoped cheese token is required")
    try:
        project_uuid = uuid.UUID(claims["p"])
    except ValueError as exc:
        raise NotFoundError("Unknown project") from exc
    summary = await ComputeGrantRepository(db).summary(project_uuid)
    state = BudgetState(
        spent=summary["credits_used"],
        limit=None if summary["unlimited"] else summary["credits_total"],
    )
    decision = decide(state)
    if not decision.allow:
        # Tell the room NOW, from the place that actually knows why (#715) —
        # rather than let Claude Code retry ten times into a `StopFailure` it
        # reads as a bad API key. `t` is the PLACE claim (room or thread); a
        # token minted without one (project-wide capabilities) has nothing to
        # tell, and a place with no turn running is the turn-start refusal
        # path's job, not this one's.
        place = claims.get("t")
        if isinstance(place, str) and place:
            try:
                place_uuid = uuid.UUID(place)
            except ValueError:
                place_uuid = None
            if place_uuid is not None:
                await chat.note_credits_refusal(place_uuid)

    # The supply decision rides along with the admission answer: the proxy has
    # to ask before every turn anyway, and one round trip that says both "may
    # it run" and "where does it go" keeps the data plane from needing a second
    # source of truth. Resolved even when refused — a caller that logs the
    # refusal can still say which pool it was refused against.
    project = await ProjectRepository(db).get(project_uuid)
    pool = resolve_pool(
        project.settings if project else None,
        subscription_enabled=settings.subscription_enabled,
    )
    model = claims.get("m")
    if isinstance(model, str):
        pool = SUBSCRIPTION if model.startswith("claude-") else GATEWAY
    supply: dict = {"pool": pool}
    if pool == GATEWAY and decision.allow:
        # Minted lazily and cached on the project; the proxy never holds a
        # provider key of its own, so a project whose key cannot be provisioned
        # gets no key here and the proxy refuses rather than falling back to a
        # shared credential (which would bill every project to one bucket).
        supply["key"] = await chat.project_gateway_key(project_uuid)
    if decision.allow:
        # Which identity the proxy should authenticate as on its ccproxy hop.
        # ccproxy scopes its ticket swap to the authenticated connection, so
        # relaying a machine's OWN ticket only works from that machine's
        # identity — carrying it here is what lets the proxy forward the ticket
        # untouched instead of holding a credential to swap in. Absent (an
        # unpinned room, a machine enrolled before this was recorded) means
        # "use the deployment-wide identity", i.e. exactly today's behaviour.
        #
        # The claim is a PLACE id, not necessarily a room's: a thread's per-turn
        # token carries the thread's own id, and the repository is what turns
        # that back into the room whose machine the thread runs on.
        place = claims.get("t")
        if isinstance(place, str) and place:
            try:
                place_uuid = uuid.UUID(place)
            except ValueError:
                place_uuid = None
            if place_uuid is not None:
                upstream = await ProjectMachineRepository(
                    db
                ).ccproxy_upstream_for_place(place_uuid)
                if upstream:
                    supply["upstream"] = upstream

    return ok({"allow": decision.allow, "reason": decision.reason, "supply": supply})


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
