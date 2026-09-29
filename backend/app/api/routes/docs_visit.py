"""``POST /docs/visit`` — the docs site's one beacon, and nothing else.

Its own module, not a fourth handler in ``routes/docs_site.py``: that file is
the assistant's request path (ask, the dev pass, the agent's read/search), while
this is a bare counter that answers 204 to everything. Separate files mean the
two are read, reviewed and changed apart — and ``main.py`` discovers routers by
module, so a second module costs nothing.

Why the endpoint is shaped the way it is:

* **204, always.** The caller is a page that has already rendered; there is
  nobody to show an error to, and the beacon is fire-and-forget by design. A
  refused or malformed visit is dropped, not reported.
* **Auth is optional, and best effort.** A signed-in reader is recorded by
  account; an anonymous one by the random id their browser keeps. Neither is
  required — the docs are public, so requiring a token would silently lose
  every signed-out visit.
* **No IP, no user agent.** See ``domain/docs_site/visits.py`` for what is
  stored and why. The request carries nothing about the caller beyond the two
  optional fields below.
"""

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator

from app.api.routes.admin_common import DbSession
from app.common.auth import get_optional_user_id
from app.core.redis import get_redis_client
from app.domain.docs_site import visits
from app.domain.docs_site.visits import VisitLimits

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/docs", tags=["docs"])

# The same shape the assistant accepts for a page slug: public pages only, and
# bounded, because this value reaches the report as a grouping key.
_PAGE = re.compile(r"^[a-z0-9-]{1,64}$")

_limits: VisitLimits | None = None


def visit_limits() -> VisitLimits:
    global _limits
    if _limits is None:
        _limits = VisitLimits(get_redis_client)
    return _limits


class VisitIn(BaseModel):
    # The browser's own random id (localStorage), not an identity the platform
    # issued. Absent for a beacon we cannot attribute — see the endpoint body.
    visitor: str = Field(default="", max_length=64)
    # The page the reader arrived on, by slug; developer pages are not public.
    page: str | None = None

    @field_validator("page")
    @classmethod
    def _page(cls, v: str | None) -> str | None:
        return v if v and _PAGE.match(v) else None


@router.post("/visit", status_code=204)
async def record_visit(
    body: VisitIn,
    db: DbSession,
    user_id: Annotated[int | None, Depends(get_optional_user_id)],
) -> Response:
    """Count one reader's visit to the docs, at most once a day.

    A visitor with neither an account nor a usable random id is accepted and
    ignored: the beacon has nothing we may key a day on, and inventing one would
    be worse than the gap.
    """
    who = visits.visitor_id(user_id, body.visitor)
    if who is None:
        return Response(status_code=204)
    if not await visit_limits().admit(who):
        return Response(status_code=204)
    try:
        await visits.record(db, visitor=who, user_id=user_id, page=body.page)
    except Exception:  # noqa: BLE001 — a lost counter must not reach the page
        logger.warning("recording a docs visit failed", exc_info=True)
    return Response(status_code=204)
