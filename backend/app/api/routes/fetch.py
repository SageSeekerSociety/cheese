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

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.config import settings
from app.core.errors import SystemBusyError
from app.domain.fetch.service import fetch as fetch_url

router = APIRouter(prefix="", tags=["fetch"])


class FetchIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    #: Without a prompt the caller gets the page. With one it gets an answer,
    #: which is the difference between spending tens of tokens and tens of
    #: thousands on the same article.
    prompt: str | None = Field(default=None, max_length=4000)
    #: Which room and project are asking. A sandbox token is SCOPED — it names
    #: both — so verifying it needs to know which one the caller claims to be
    #: speaking for. An endpoint that touches none of our resources still has to
    #: say this, because the check is on the credential, not on the resource.
    topic: uuid.UUID | None = None
    project: uuid.UUID | None = None


@router.post("/fetch", summary="Read a URL for an agent")
async def read_url(body: FetchIn, actor: ActorResolverDep) -> dict:
    # A signed-in person or an agent's scoped token; never anonymous. The
    # backend reads from inside the platform's network, so an open fetch is an
    # open door into it for anyone on the internet — `guard` keeps the reads to
    # public addresses, and this keeps the door itself shut to strangers. There
    # is no per-resource permission beyond that: no resource of ours is touched.
    # The topic still has to be passed: a scoped sandbox token only verifies
    # against the scope it was minted for.
    await actor.require_verified_caller(topic_id=body.topic, project_id=body.project)

    distill = None
    if settings.anthropic_base_url and settings.anthropic_auth_token:
        # `agent_model` and not `agent_haiku_model`. The haiku setting is an
        # ALIAS the CLI resolves internally — it names what "haiku" should map
        # to for a session, and its default names a model this gateway may not
        # serve at all. Asking the gateway for it returned
        # "Invalid model name passed in model=glm-4.5-air", the distillation
        # failed, and every prompted fetch quietly returned the whole page
        # instead of an answer. `agent_model` is the model this deployment
        # actually runs, so it is the one that is certainly reachable.
        distill = (
            settings.anthropic_base_url,
            settings.anthropic_auth_token,
            settings.agent_model,
        )

    outcome = await fetch_url(
        body.url,
        body.prompt,
        reader_endpoint=settings.fetch_reader_endpoint,
        browser_endpoint=settings.fetch_browser_endpoint,
        distill=distill,
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


class PageCheckIn(BaseModel):
    #: The page itself, not a path: the agent checks what it is about to show,
    #: before it is shown, and the file is on its machine rather than ours.
    html: str = Field(min_length=1, max_length=5_000_000)
    widths: list[int] = Field(default=[400, 1280], min_length=1, max_length=3)
    color_scheme: str = Field(default="light", pattern="^(light|dark)$")
    topic: uuid.UUID | None = None
    project: uuid.UUID | None = None


#: Two widths at a second or two each, plus a cold browser on the first call.
PAGE_CHECK_TIMEOUT_S = 90.0


@router.post("/page-check", summary="Render a page an agent made and report it")
async def check_page(body: PageCheckIn, actor: ActorResolverDep) -> dict:
    """The platform's browser looks at a page so the agent does not need one.

    Full-page screenshots at each width, how wide the page really is, what
    pushes it wider than the screen, and what failed to load or run. The same
    shared browser as `/fetch`, under the same public-only egress, so the page's
    own requests cannot reach into this network either.
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
                },
            )
    except httpx.HTTPError as exc:
        raise SystemBusyError(f"Page renderer unreachable: {type(exc).__name__}") from exc
    if r.status_code != 200:
        raise SystemBusyError(f"Page renderer answered HTTP {r.status_code}")
    return ok(r.json())
