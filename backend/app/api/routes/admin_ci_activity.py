"""CI 与合并队列: what the platform's own runners and merge queue did.

Three reads, no writes. Each reads the tables the CI activity sweep fills
(`domain/ci_activity/ingest.py`) and never GitHub, so opening the page costs the
installation's quota nothing. There is no refresh button: the sweep runs every
few minutes, and a button would be one more way to spend the quota the people
delivering cards share.

* ``GET /admin/ci-activity`` — the page: headline figures, daily and hourly
  series, the runners, the queue's ejections, and how far the sweep has got.
* ``GET /admin/ci-activity/jobs`` — per job name, optionally on one runner.
* ``GET /admin/ci-activity/steps`` — per step of one job name.

Behind `PlatformAdminDep` like the rest of `/admin`: this is the platform's own
CI, not any project's. The definitions of every figure are in
`domain/ci_activity/board.py`.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.admin_deps import PlatformAdminDep
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.config import settings
from app.domain.ci_activity import board

router = APIRouter(
    prefix="/admin/ci-activity", tags=["admin"], dependencies=[ROUTE_DECIDES]
)

#: The sweep keeps 30 days (`ci_activity.ingest.WINDOW`); a longer window would
#: read as a quiet start of the month.
Days = Annotated[int, Query(ge=1, le=30)]


@router.get("")
async def ci_activity_board(
    db: DbSession, handle: PlatformAdminDep, days: Days = 7
) -> dict:
    return ok(await board.build_board(db, settings.platform_repository, days=days))


@router.get("/jobs")
async def ci_activity_jobs(
    db: DbSession,
    handle: PlatformAdminDep,
    days: Days = 7,
    runner: Annotated[str | None, Query(max_length=300)] = None,
) -> dict:
    return ok(
        {
            "jobs": await board.job_table(
                db, settings.platform_repository, days=days, runner=runner
            )
        }
    )


@router.get("/steps")
async def ci_activity_steps(
    db: DbSession,
    handle: PlatformAdminDep,
    job: Annotated[str, Query(min_length=1, max_length=300)],
    days: Days = 7,
    runner: Annotated[str | None, Query(max_length=300)] = None,
) -> dict:
    return ok(
        {
            "job": job,
            "steps": await board.step_table(
                db, settings.platform_repository, days=days, job=job, runner=runner
            ),
        }
    )
