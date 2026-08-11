"""Scheduler route — manual tick (the loop runs automatically; this lets you
trigger 定期巡检 on demand, spec §9.1)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_scheduler_service
from app.api.response import ok
from app.domain.scheduler.service import SchedulerService

router = APIRouter(prefix="/api/admin/scheduler", tags=["scheduler"])

SchedulerDep = Annotated[SchedulerService, Depends(get_scheduler_service)]


@router.post("/tick")
async def tick(scheduler: SchedulerDep) -> dict:
    return ok(await scheduler.tick())


@router.post("/poll-open-prs")
async def poll_open_prs(scheduler: SchedulerDep) -> dict:
    """两阶段采纳 (PR迭代式): manually trigger one round of the PR/deploy
    poller (the loop itself runs on `accept_pr_poll_interval_s` — same
    on-demand-trigger shape as /tick)."""
    return ok(await scheduler.poll_open_prs())
