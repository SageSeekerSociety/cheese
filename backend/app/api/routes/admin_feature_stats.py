"""功能数据: the catalogue, and one report per feature.

The fifth module in `/admin` next to the queue, the dashboard, the model ledger
and the members. Same gate as the rest (`admin_common.PlatformAdminDep`) — a
feature's numbers say how the platform is used and by how many people, which is
not something a signed-in stranger is owed.

Two endpoints, and their split is the design:

* ``GET /admin/feature-stats`` — the catalogue. **It carries no numbers**: only
  the ids, titles and one-line summaries of the features that have a page. A
  catalogue with figures on it is a page of charts nobody reads, and the reason
  to open this area is to look at one feature, not to scan all of them.
* ``GET /admin/feature-stats/{id}?days=30`` — one feature's report, from the
  loader its registry entry names. Each feature's shape is its own; the front
  end's page is written against it.

``days`` is bounded the same way the dashboard's is (1..90) and for the same
reason: the report reads tables that grow with use, and 「平台开板以来」 is not
a window anyone can afford to aggregate. The default is 30 rather than the
dashboard's 7 because this page is read once in a while, to decide something,
not glanced at daily.
"""

from fastapi import APIRouter, Query

from app.api.response import ok
from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.feature_stats import registry

router = APIRouter(prefix="/admin/feature-stats", tags=["admin"])


@router.get("")
async def feature_catalogue(handle: PlatformAdminDep) -> dict:
    """Which features have a data page. No numbers — see the module docstring."""
    return ok({"features": registry.catalogue()})


@router.get("/{feature_id}")
async def feature_report(
    feature_id: str,
    db: DbSession,
    handle: PlatformAdminDep,
    days: int = Query(default=30, ge=1, le=90),
) -> dict:
    """One feature's report for the last ``days`` days, including today."""
    feature = registry.find(feature_id)
    if feature is None:
        raise NotFoundError(say("featureStatsNotFound", feature=feature_id))
    return ok(await feature.load(db, days=days))
