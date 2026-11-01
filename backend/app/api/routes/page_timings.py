"""How long a page took in the reader's own browser, request by request.

The backend's own timing (Server-Timing) says how long it computed; it cannot
see the network between a reader and the deployment, the edge they came in by,
or what the page waited on before it drew. A page that answers in 0.2 s on the
box can still take seconds to someone across the Hong Kong detour. The browser
reports what it saw (`frontend/src/lib/pageTiming.ts`), and a platform admin
reads the last few hundred reports back.

Kept in Redis, capped and expiring: a diagnostic, not a record anyone keeps.
"""

import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.admin_deps import PlatformAdminDep
from app.api.response import ok, typed_response
from app.api.write_access import Can, InProject
from app.core.redis import get_redis_client
from app.domain.project_permissions import Permission

router = APIRouter(tags=["page-timings"])

_KEY = "cheesex:page-timings"
#: Reports kept, newest first, and for how long after the last one.
KEPT = 500
KEPT_FOR_S = 7 * 24 * 3600


class RequestTiming(BaseModel):
    #: The API path with ids folded to ``:id`` by the browser.
    path: str = Field(max_length=200)
    #: Milliseconds from the page's navigation to the request's start.
    start: float = Field(ge=0, le=600_000)
    #: Milliseconds from start to the last byte.
    duration: float = Field(ge=0, le=600_000)
    #: Milliseconds from start to the first byte of the answer, when known.
    ttfb: float | None = Field(default=None, ge=0, le=600_000)
    #: Bytes on the wire, 0 when the browser may not say (another origin).
    bytes: int = Field(default=0, ge=0, le=1 << 31)


class PageTimingIn(BaseModel):
    #: The route the reader opened, with ids folded (``/projects/:id/overview``).
    route: str = Field(max_length=200)
    #: The host the page came from: which entry the reader used.
    host: str = Field(max_length=100)
    #: Whether this was the first page of the visit (scripts downloaded too).
    first_load: bool = False
    #: Milliseconds until the page's own scripts finished loading, first load only.
    loaded: float | None = Field(default=None, ge=0, le=600_000)
    requests: list[RequestTiming] = Field(default_factory=list, max_length=80)


class PageTimingOut(PageTimingIn):
    at: float
    handle: str


@router.post("/projects/{project_id}/page-timings", **typed_response(dict))
async def report_page_timing(
    at: Annotated[InProject, Can(Permission.speak)], body: PageTimingIn
) -> dict[str, Any]:
    """Keep one page's timing as the reader's browser measured it."""
    redis = get_redis_client()
    if redis is not None:
        row = {**body.model_dump(), "at": time.time(), "handle": at.actor.handle}
        try:
            pipe = redis.pipeline()
            pipe.lpush(_KEY, json.dumps(row, ensure_ascii=False))
            pipe.ltrim(_KEY, 0, KEPT - 1)
            pipe.expire(_KEY, KEPT_FOR_S)
            await pipe.execute()
        except Exception:  # a diagnostic that fails must not fail the page
            pass
    return ok({})


@router.get("/admin/page-timings", **typed_response(list[PageTimingOut]))
async def read_page_timings(
    _admin: PlatformAdminDep,
    limit: Annotated[int, Query(ge=1, le=KEPT)] = 100,
    route: str | None = None,
) -> dict[str, Any]:
    """The newest reports, optionally of one route, for a platform admin."""
    redis = get_redis_client()
    if redis is None:
        return ok([])
    raw = await redis.lrange(_KEY, 0, KEPT - 1)
    rows = [json.loads(item) for item in raw]
    if route:
        rows = [row for row in rows if row.get("route") == route]
    return ok(rows[:limit])
