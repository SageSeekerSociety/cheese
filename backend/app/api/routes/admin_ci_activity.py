"""CI activity: what the platform's own runners and merge queue did.

Three reads, no writes. Each reads the tables the CI activity sweep fills
(`domain/ci_activity/ingest.py`) and never GitHub, so opening the page costs the
installation's quota nothing. There is no refresh button: the sweep runs every
few minutes, and a button would be one more way to spend the quota the people
delivering cards share.

* ``GET /admin/ci-activity`` — the page: headline figures, daily and hourly
  series, the runners, the queue's ejections, and how far the sweep has got.
* ``GET /admin/ci-activity/jobs`` — per workflow/job, optionally on one runner.
* ``GET /admin/ci-activity/steps`` — per step of one workflow's job.

Behind `PlatformAdminDep` like the rest of `/admin`: this is the platform's own
CI, not any project's. The definitions of every figure are in
`domain/ci_activity/board.py`.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.admin_deps import PlatformAdminDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.config import settings
from app.domain.ci_activity import board
from app.domain.ci_activity.schemas import CiBoardOut, CiJobsOut, CiStepsOut

router = APIRouter(
    prefix="/admin/ci-activity", tags=["admin"], dependencies=[ROUTE_DECIDES]
)

#: The sweep keeps 30 days (`ci_activity.ingest.WINDOW`); a longer window would
#: read as a quiet start of the month.
Days = Annotated[int, Query(ge=1, le=30)]


@router.get("", **typed_response(CiBoardOut))
async def ci_activity_board(
    db: DbSession, handle: PlatformAdminDep, days: Days = 7
) -> dict:
    repo = settings.platform_repository
    return ok(
        await board.kept_board(
            repo, days, lambda: board.build_board(db, repo, days=days)
        )
    )


@router.get("/jobs", **typed_response(CiJobsOut))
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


@router.get("/steps", **typed_response(CiStepsOut))
async def ci_activity_steps(
    db: DbSession,
    handle: PlatformAdminDep,
    job: Annotated[str, Query(min_length=1, max_length=300)],
    workflow: Annotated[str, Query(min_length=1, max_length=300)],
    days: Days = 7,
    runner: Annotated[str | None, Query(max_length=300)] = None,
) -> dict:
    return ok(
        {
            "job": job,
            "workflow": workflow,
            "steps": await board.step_table(
                db,
                settings.platform_repository,
                days=days,
                workflow=workflow,
                job=job,
                runner=runner,
            ),
        }
    )
