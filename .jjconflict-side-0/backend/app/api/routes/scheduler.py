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
