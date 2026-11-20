"""Fetch a web page on an agent's behalf.

Reading the web belongs on this side of the boundary. The sandbox reaches the
internet through a datacentre egress that several sites refuse outright; giving
each topic its own browser would mean one Chrome install per topic; and nothing
either of them fetched could be shared or rate-limited as a whole. Moving the
read here fixes all three at once, and it is the only place a credentialed fetch
could ever live — an agent that held a cookie could be talked into leaking it by
the very page it was asked to read.

The sandbox therefore sends a URL and receives prose. It never receives the
means to fetch anything itself.
"""

import uuid
from typing import Annotated, NamedTuple

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.auth.resolver import ActorResolver
from app.api.deps import get_chat_service
from app.api.model_keys import credit_refusal, task_credit_context_of
from app.api.refusal import refuse
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core import model_roles
from app.core.config import settings
from app.core.errors import ForbiddenError, SystemBusyError
from app.core.sentences import say
from app.domain.agent.chat import ChatService
from app.domain.agent.personal.keys import stored_key_and_model
from app.domain.fetch.search import MAX_QUERY_CHARS, SearchRefused, SearchUnavailable
from app.domain.fetch.search import search as search_web
from app.domain.fetch.service import fetch as fetch_url
from app.domain.identity.actor import Actor
from app.domain.project.services import ProjectService
from app.domain.usage.balance import Refusal

router = APIRouter(prefix="", tags=["fetch"])


class FetchIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    #: Without a prompt the caller gets the page. With one it gets an answer,
    #: which is the difference between spending tens of tokens and tens of
    #: thousands on the same article.
    prompt: str | None = Field(default=None, max_length=4000)
    #: Go to the platform's browser first: the caller read the page and found
    #: only what scripts would have replaced (an app shell, a cookie wall).
    render: bool = False
    #: Which room and project are asking. A sandbox token is SCOPED — it names
    #: both — so verifying it needs to know which one the caller claims to be
    #: speaking for. An endpoint that touches none of our resources still has to
    #: say this, because the check is on the credential, not on the resource.
    topic: uuid.UUID | None = None
    project: uuid.UUID | None = None


@router.post("/fetch", summary="Read a URL for an agent", dependencies=[ROUTE_DECIDES])
async def read_url(
    body: FetchIn,
    db: DbSession,
    actor: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    # A signed-in person or an agent's scoped token; never anonymous. The
    # backend reads from inside the platform's network, so an open fetch is an
    # open door into it for anyone on the internet — `guard` keeps the reads to
    # public addresses, and this keeps the door itself shut to strangers. There
    # is no per-resource permission beyond that: no resource of ours is touched.
    # The topic still has to be passed: a scoped sandbox token only verifies
    # against the scope it was minted for.
    caller = await actor.require_verified_caller(
        topic_id=body.topic, project_id=body.project
    )

    # Answering the prompt is a model call, so it is made on the caller's own
    # gateway key, admitted and charged with the work's other model calls
    # (`_caller_key`). Reading the page costs the payer nothing; a caller with
    # no key, or a payer out of credits, still gets the page, just not an
    # answer drawn from it.
    distill = None
    if body.prompt and settings.anthropic_base_url:
        spend = await _caller_key(
            db, chat, actor, caller, topic=body.topic, project=body.project
        )
        if spend.key:
            distill = (settings.anthropic_base_url, spend.key, spend.model)

    outcome = await fetch_url(
        body.url,
        body.prompt,
        reader_endpoint=settings.fetch_reader_endpoint,
        browser_endpoint=settings.fetch_browser_endpoint,
        distill=distill,
        render=body.render,
    )
    return ok(
        {
            "url": outcome.url,
            "ok": outcome.ok,
            "text": outcome.text,
            "rung": outcome.rung,
            "distilled": outcome.distilled,
            # The trail is returned on success too. Which rung answered is how
            # anyone later notices coverage drifting, and a failure without it
            # is a fetch service nobody can improve.
            "trail": outcome.trail(),
        }
    )


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    date: str | None = None


class WebSearchOut(BaseModel):
    results: list[WebSearchResult]


class CallerKey(NamedTuple):
    """The gateway key a caller's spend goes on, the model that key may call,
    and why it may not spend now."""

    key: str | None
    model: str
    refusal: Refusal | None = None


async def _caller_key(
    db: AsyncSession,
    chat: ChatService,
    resolver: ActorResolver,
    actor: Actor,
    *,
    topic: uuid.UUID | None,
    project: uuid.UUID | None,
) -> CallerKey:
    """The gateway key this caller's model calls are made on — a search, and
    the answer a prompted fetch draws from its page, are made on it too — and
    why it may not spend now.

    A person's own 芝士 (a delegated credential naming no agent) spends on
    that person's key, with the one model that key was minted for
    (``personal.keys``); whether they may spend was judged when they asked
    (``assistant.ask``), as for its model calls. An agent spends on the key
    its model calls use, admitted as they are (``model_keys``): a room agent's
    scoped token names its project and, for a task, whose credits it spends; a
    project credential authenticates only for the project the request names;
    a document question's answer is its project's work. Those keys take the
    model this deployment runs (``agent_model``), never the haiku alias: that
    names what a session maps "haiku" to and may not exist on the gateway, and
    asking for it failed every distillation. Anyone else — a person
    signed in, the global sandbox override — has no key."""
    agent_model = model_roles.model("agent_model")
    delegation = resolver.delegation()
    claims: dict = {}
    if delegation is not None:
        if delegation.agent is None:
            stored = (
                await stored_key_and_model(db, delegation.user_id)
                if delegation.user_id is not None
                else None
            )
            if stored is None:
                return CallerKey(None, agent_model)
            return CallerKey(*stored)
        named = uuid.UUID(delegation.project_id) if delegation.project_id else None
    elif actor.via == "cheese":
        claims = resolver.credential_claims() or {}
        named = (
            (uuid.UUID(claims["p"]) if claims.get("p") else None)
            or project
            or (await resolver.project_of_topic(topic) if topic else None)
        )
    else:
        return CallerKey(None, agent_model)
    owner = await ProjectService(db).get(named) if named else None
    if owner is None:
        return CallerKey(None, agent_model)
    context = await task_credit_context_of(db, owner, claims)
    refused = await credit_refusal(db, owner, context)
    if refused is not None:
        return CallerKey(None, agent_model, refused)
    if context is not None:
        key = await chat.project_gateway_key(
            owner.id,
            task_id=context.task_id,
            credit_owner_handle=context.owner_handle,
        )
    else:
        key = await chat.project_gateway_key(owner.id)
    return CallerKey(key, agent_model)


@router.get(
    "/web-search",
    summary="Search the web for an agent",
    dependencies=[ROUTE_DECIDES],
    **typed_response(WebSearchOut),
)
async def web_search(
    db: DbSession,
    actor: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_CHARS)],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
    topic: uuid.UUID | None = None,
    project: uuid.UUID | None = None,
):
    """Results (title, URL, summary, date) from the gateway's DeepSeek search.

    The same caller rule as `/fetch`: a signed-in person or an agent's scoped
    token, which verifies only against the room it names. The search is made
    on the caller's own gateway key (``_caller_key``), so it is admitted,
    priced and charged with that key's model calls: a payer out of credits is
    refused before DeepSeek is asked, and the key's spend, the search's
    included, is drained into credits when the turn or question is charged.
    """
    caller = await actor.require_verified_caller(topic_id=topic, project_id=project)
    if not settings.anthropic_base_url:
        raise SystemBusyError(say("webSearchNotConfigured"))
    spend = await _caller_key(db, chat, actor, caller, topic=topic, project=project)
    if spend.refusal is not None:
        return refuse(429, spend.refusal.message, spend.refusal.retry_after_s())
    if not spend.key:
        raise ForbiddenError(say("webSearchNoKey"))
    try:
        found = await search_web(
            q, gateway=settings.anthropic_base_url, key=spend.key, limit=limit
        )
    except SearchRefused:
        return refuse(429, say("webSearchBudgetSpent"))
    except SearchUnavailable as exc:
        raise SystemBusyError(say("webSearchUnavailable", reason=str(exc))) from exc
    return ok({"results": [vars(r) for r in found]})


class PageCheckIn(BaseModel):
    #: The page itself, not a path: the agent checks what it is about to show,
    #: before it is shown, and the file is on its machine rather than ours.
    html: str = Field(min_length=1, max_length=5_000_000)
    widths: list[Annotated[int, Field(ge=200, le=2000)]] = Field(
        default=[400, 1280], min_length=1, max_length=3
    )
    color_scheme: str = Field(default="light", pattern="^(light|dark)$")
    #: Also print an A4 PDF with these margins; see browser-render's `/inspect`.
    pdf_margin: str | None = Field(
        default=None,
        pattern=r"^[0-9.]+(mm|cm|in|px)?( [0-9.]+(mm|cm|in|px)?)?$",
    )
    topic: uuid.UUID | None = None
    project: uuid.UUID | None = None


#: Two widths at a second or two each, plus a cold browser on the first call.
PAGE_CHECK_TIMEOUT_S = 90.0


@router.post(
    "/page-check",
    summary="Render a page an agent made and report it",
    dependencies=[ROUTE_DECIDES],
)
async def check_page(body: PageCheckIn, actor: ActorResolverDep) -> dict:
    """The platform's browser looks at a page so the agent does not need one.

    Full-page screenshots at each width, how wide the page really is, what
    pushes it wider than the screen, and what failed to load or run. Rendered
    by the browser-render service that also serves `/fetch` (its own browser
    there, under the same public-only egress), so the page's own requests
    cannot reach into this network either.
    """
    await actor.require_verified_caller(topic_id=body.topic, project_id=body.project)
    endpoint = settings.fetch_browser_endpoint
    if not endpoint:
        raise SystemBusyError("No page renderer is configured on this deployment")
    try:
        async with httpx.AsyncClient(timeout=PAGE_CHECK_TIMEOUT_S) as client:
            r = await client.post(
                endpoint.rstrip("/") + "/inspect",
                json={
                    "html": body.html,
                    "widths": body.widths,
                    "color_scheme": body.color_scheme,
                    "pdf_margin": body.pdf_margin,
                },
            )
    except httpx.HTTPError as exc:
        raise SystemBusyError(
            f"Page renderer unreachable: {type(exc).__name__}"
        ) from exc
    if r.status_code != 200:
        raise SystemBusyError(f"Page renderer answered HTTP {r.status_code}")
    return ok(r.json())
