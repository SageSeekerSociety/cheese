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

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.config import settings
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
    # Any caller the platform already authenticates may read a public page;
    # there is no per-resource permission to check because no resource of ours
    # is being touched. The topic still has to be passed: a scoped sandbox token
    # only verifies against the scope it was minted for, so leaving it out made
    # every sandbox call fail with "Agent credential is invalid or expired".
    await actor.resolve(
        fallback_handle=None, topic_id=body.topic, project_id=body.project
    )

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
