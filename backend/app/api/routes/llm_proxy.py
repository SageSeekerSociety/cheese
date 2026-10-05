"""Admission, and the model route for harnesses that speak to a base URL.

``/llm/admission`` is THE control point (结论 46). Claude Code machines hold no
base URL at all: they reach the metering proxy over ``HTTPS_PROXY``, and the
proxy asks here, per request, whether the turn may run, which pool serves it and
which model name to write into its body. Nothing about that is signed into a
launch environment, so a binding changed on a card takes effect on the next
request rather than the next screen.

The catch-all below serves the harnesses that CANNOT be steered that way — Codex
and Pi are pointed at ``{api_base}/llm/v1``. A MicroCloud machine cannot reach
the pool gateway itself (it listens on a box-local address), and handing the
machine a provider key would put a shared credential on hardware the platform
does not control and make spend unattributable. So the machine carries only its
own scoped cheese token; this route authenticates it, swaps in the project's
virtual gateway key — the same key its local turns run on, so budget and
attribution are unchanged — and streams the upstream response back verbatim. The
credential never leaves the box. A person's 芝士 comes here the same way with a
personal credential instead, and is given that person's own key (``_person_key``).

Protocol-agnostic on purpose: whatever path the client asks for is forwarded
as-is, so a client-side protocol change needs no change here.
"""

import logging
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_seated_in_its_room
from app.api.deps import get_chat_service, get_db
from app.api.response import ok
from app.core.config import settings
from app.core.db import async_session_factory, release_read_session
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    GatewayUnavailableError,
    NotFoundError,
    ValidationError,
)
from app.core.sandbox_auth import (
    PersonalClaims,
    is_global_sandbox_token,
    personal_claims,
    scoped_token_claims,
)
from app.core.sentences import listing, say
from app.domain.agent.chat import ChatService
from app.domain.agent.credits_notice import note_credits_refusal
from app.domain.agent.personal.keys import stored_key
from app.domain.agent.supply import GATEWAY
from app.domain.agent_instance import configuration
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.policy import gate
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task import binding
from app.domain.usage.services import UsageService

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


def _default_catalog_id(choices: dict[str, dict]) -> str | None:
    default = next((c for c in choices.values() if c.get("default")), None)
    return default["id"] if default else None


def _offerable_model_ids(project, choices: dict[str, dict]) -> set[str]:
    """这个项目能指定的模型集合：目录里属于本项目池的那些 id。

    池外的目录项（gateway 项目里的订阅短名、订阅项目里的网关模型）对主
    agent 不可指定：绑上它们请求就得走另一条供给路，而那条路这个项目没有
    凭据 —— 列进可选等于教人吃拒绝。
    """
    pool = configuration.project_pool(project.settings if project else None)
    return {cid for cid, c in choices.items() if c.get("supply") == pool}


async def _bind_requested_subagent_model(
    agents,
    project,
    choices: dict[str, dict],
    requested: str,
    parent_handle: str | None,
):
    """分身指定了模型时的绑定：翻译成目录 id，校验它在项目模型目录内。

    指定模型是 subagent 机制自己的能力，与平台 AI 队友（有身份的参与者）
    无关 —— 可指定的集合是项目的模型目录，不是任何参与者的配置（2026-09-23
    拍板）。指定了就要么绑它、要么明说为什么不行 —— 静默改写回默认模型正是
    「指定了却不生效」那个旧行为（I27 的另一种长相）。

    体里的名字就是这个分身的模型，没有「回显」要认：会话启动时
    ``CLAUDE_CODE_SUBAGENT_MODEL`` 钉着分身默认（``agent/chat.py``），没指定
    的分身体里写的就是它；和父会话同名只能是主 agent 指定了父会话那个模型。
    父会话自己在跑的模型因此总是可指定的，跨池也一样 —— 它已经在这个席位
    上跑着了。
    """
    requested_id = binding.catalog_id(requested, choices)
    parent = await agents.for_seat_handle(project, parent_handle)
    if parent is None:
        parent = await agents.for_project(project)
    settings_ = project.settings or {}
    # The same order the parent's own admission resolves in: seat, project
    # default, catalogue default.
    parent_id = (
        ((parent.configuration or {}).get("model") if parent else None)
        or settings_.get("default_model")
        or _default_catalog_id(choices)
    )
    allowed = _offerable_model_ids(project, choices)
    # 父会话的模型和项目显式配的两个默认（主模型、分身默认）也合法：它们跨
    # 池也真跑得起来（供给跟着绑定走），主 agent 复述它们不该吃到一个拒绝。
    # 但只列目录里还在的 —— 列一个 resolve 绑不上的,是把人从一个拒绝指到另
    # 一个拒绝。
    for configured in (
        parent_id,
        settings_.get("default_model"),
        settings_.get("default_subagent_model"),
    ):
        if isinstance(configured, str) and configured and configured in choices:
            allowed.add(configured)
    offer = listing(sorted(allowed)) if allowed else say("noModelToOffer")
    if requested_id is None:
        raise ValidationError(
            say("subagentModelNotInCatalog", model=repr(requested), offer=offer)
        )
    if requested_id not in allowed:
        raise ValidationError(
            say("subagentModelNotAllowed", model=repr(requested), offer=offer)
        )
    return binding.resolve(None, choices, agent_model=requested_id)


async def _require_model_caller(
    db: AsyncSession, token: str, project_id: uuid.UUID
) -> None:
    """Refuse a credential whose agent is no longer seated where it acts.

    A document question's session is the one exception: one asked in a room
    that seats no agent is answered by the project's own agent, which sits in
    no room for it (``document.question.bind``). Its credential names the
    document (``d``) and the project's own agent, and is let through on that.
    A room session's credential names no document, so the project's own agent
    taken off a room is refused like any other.
    """
    claims = scoped_token_claims(token) or {}
    if claims.get("d") and await _projects_own_agent(db, project_id, claims.get("a")):
        return
    await require_seated_in_its_room(db, token, project_id=project_id)


async def _projects_own_agent(
    db: AsyncSession, project_id: uuid.UUID, handle: object
) -> bool:
    project = await ProjectRepository(db).get(project_id)
    if project is None or project.default_agent_instance_id is None:
        return False
    return any(
        handle in (agent.handle, agent_instance_handle(agent.id))
        for agent in await AgentInstanceService(db).list_for_project(project_id)
        if agent.id == project.default_agent_instance_id
    )


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
    gateway key rides along only when the proxy's own credential does too. The
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
    project = await ProjectRepository(db).get(project_uuid)
    if project is not None:
        try:
            await _require_model_caller(db, token, project_uuid)
        except (AuthenticationRequiredError, ForbiddenError) as exc:
            # Answered, not raised: the metering proxy reads any non-200 as the
            # backend being unreachable and lets the request through (fail-open).
            # `binding` is the kind it renders as a 400 that is not retried, with
            # this reason; a `budget` refusal would tell the agent to wait for
            # credits that are not what is missing.
            return ok(
                {
                    "allow": False,
                    "reason": str(exc),
                    "reason_kind": "binding",
                    "supply": {},
                }
            )
    refused = None
    if project is not None:
        refused = await UsageService(db).admit_project(project_uuid)
    # The supply decision rides along with the admission answer: the proxy has
    # to ask before every turn anyway, and one round trip that says both "may
    # it run" and "where does it go" keeps the data plane from needing a second
    # source of truth. Resolved even when refused — a caller that logs the
    # refusal can still say which pool it was refused against.
    #
    # Which pool comes from the model binding, resolved HERE, per request. It
    # used to come from a model name signed into the caller's session
    # credential at launch — a claim minted once, hours ago, that no later
    # change could reach, and a second place declaring the same thing as the
    # binding on the card. This is the one control point (结论 46): a request
    # whose model cannot be resolved is refused and told so, never quietly
    # served from the other pool.
    #
    # 答不出就拒绝，不换池（I27）。A deployment whose catalogue cannot name a
    # model for this project has no second pool to quietly serve the request
    # from — that silent swap is what one control point exists to remove — so
    # the refusal carries the resolver's own words and the turn stops here.
    is_subagent = request.headers.get("x-cheese-subagent") == "1"
    # 主 agent 开分身时指定的模型：CC 把它写进分身请求体的顶层 model 成员，计量
    # 代理解析出来随本调用带上来。只在分身路径上读 —— 主对话的模型从来由绑定
    # 决定，请求体里那个名字不是输入。
    child_model = (
        (request.headers.get("x-cheese-child-model") or "").strip()
        if is_subagent
        else ""
    )
    requested = (
        child_model or (request.headers.get("x-cheese-requested-model") or "").strip()
    )
    agents = AgentInstanceService(db)
    agent = None
    if project is not None and not is_subagent:
        agent = await agents.for_seat_handle(project, claims.get("a"))
        if agent is None:
            agent = await agents.for_project(project)
    choices = binding.catalog(project.settings if project else None)
    try:
        if project is not None and is_subagent and requested:
            bound = await _bind_requested_subagent_model(
                agents,
                project,
                choices,
                requested,
                claims.get("a"),
            )
        else:
            bound = binding.resolve(
                None,
                choices,
                agent_model=agent.configuration.get("model") if agent else None,
                default_model=(
                    (project.settings or {}).get("default_subagent_model")
                    if project and is_subagent
                    else None
                )
                or ((project.settings or {}).get("default_model") if project else None),
            )
        if is_subagent and requested and project:
            choice = choices[bound.model]
            place = claims.get("t")
            try:
                place_id = uuid.UUID(place) if place else None
            except ValueError:
                place_id = None
            outcome = await chat._pass_policy_gate(
                db,
                place_id,
                gate.Call(
                    resource=gate.Resource.model,
                    subject=bound.model,
                    label=choice["label"],
                    tier=choice["tier"],
                    approver=project.owner_handle or "",
                ),
                gate.policy_of(
                    project.settings,
                    await UsageService(db).plan_model_tiers(project.team_id),
                ),
                actor=claims.get("a") or "",
            )
            if outcome is not None:
                await db.commit()
                raise ValidationError(outcome.proposal.content)
    except ValidationError as exc:
        # `reason_kind` is what stops the proxy dressing this up as a budget
        # refusal: it renders every `allow=false` it has ever seen as a 429
        # `rate_limit_error` prefixed "cheese project budget: …", and a project
        # whose card names a model the catalogue cannot serve would be told its
        # quota ran out. The refusal has to say the true reason to satisfy I27
        # at all — a refusal nobody can act on is the silent swap wearing a
        # different hat.
        return ok(
            {
                "allow": False,
                "reason": exc.message,
                "reason_kind": "binding",
                "supply": {},
            }
        )
    pool = bound.supply
    # The name that goes into the REQUEST BODY. The proxy writes it there on the
    # way out, which is the only place either pool reads a model from, and the
    # only reason the launch environment can now name none. A subscription model
    # is catalogued under a short id (`sonnet`) and served under its full name;
    # the catalogue is the only thing that knows, so the translation lives on
    # the binding (`WorkBinding.wire_model`) rather than here.
    supply: dict = {"pool": pool, "model": bound.wire_model}

    # The calls below may wait on the gateway lock and open another database
    # session. Returning this read connection first prevents concurrent
    # admissions from exhausting the pool while they wait.
    await release_read_session(db)
    if refused is not None:
        # Tell the running room when credits run out (#715); the room write
        # uses its own database session.
        place = claims.get("t")
        if isinstance(place, str) and place:
            try:
                place_uuid = uuid.UUID(place)
            except ValueError:
                place_uuid = None
            if place_uuid is not None:
                await note_credits_refusal(
                    chat.session_factory, place_uuid, refused.message
                )
    # The key goes only to the metering proxy, which proves itself with its
    # own credential beside the session's bearer. The bearer alone names the
    # room, and the session holds that same token: answering it with the key
    # would put a shared pool credential inside every room that can reach this
    # route. Anyone else (a pi runner admitting a subagent) gets the decision.
    if (
        pool == GATEWAY
        and refused is None
        and is_global_sandbox_token(request.headers.get("x-cheese-token") or "")
    ):
        # Minted lazily and cached on the project; never keep the admission
        # read connection checked out while waiting for the gateway.
        supply["key"] = await chat.project_gateway_key(project_uuid)

    return ok(
        {
            "allow": refused is None,
            "reason": "admitted" if refused is None else str(refused.message),
            "reason_kind": "budget",
            # The proxy turns this into the reset headers Claude Code reads, so
            # a refused turn says when it can run again and is not retried
            # before then.
            "reopens_at": (
                int(refused.reopens_at.timestamp())
                if refused is not None and refused.reopens_at is not None
                else None
            ),
            "supply": supply,
        }
    )


async def _person_key(claims: PersonalClaims) -> str | None:
    """The key a person's 芝士 calls the model on: their own, never the pool's
    credential or a project's. A call made with a person's credential is that
    person's, and is charged to them; whether they may ask at all is judged
    when they ask (``assistant.ask``)."""
    async with async_session_factory() as session:
        return await stored_key(session, claims.user_id)


@router.api_route("/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def proxy(
    path: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> StreamingResponse:
    token = _caller_token(request)
    person = personal_claims(token) if token else None
    claims = scoped_token_claims(token) if token and person is None else None
    if person is None and (not claims or not claims.get("p")):
        raise AuthenticationRequiredError("A scoped cheese token is required")
    if not settings.anthropic_base_url:
        raise GatewayUnavailableError("No model pool is configured for this deployment")

    if person is not None:
        project_id = f"user:{person.user_id}"
        key = await _person_key(person)
    else:
        assert claims is not None
        project_id = claims["p"]
        try:
            project_uuid = uuid.UUID(project_id)
        except ValueError as exc:
            raise NotFoundError("Unknown project") from exc
        await _require_model_caller(db, token, project_uuid)
        # The stream below can hold this request for minutes.
        await release_read_session(db)
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
