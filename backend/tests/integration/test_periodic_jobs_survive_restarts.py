"""A long-interval job still runs on a backend restarted more often than it is due."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.background import PeriodicRunner
from app.core.job_runs import JobRun, JobRuns

pytestmark = pytest.mark.anyio


async def _process(db_factory, job, seconds: float) -> None:
    """One backend process holding the jobs for a while, then going away."""
    runner = PeriodicRunner("hourly cleanup", 3600, job)
    runner.start(JobRuns(db_factory))
    await asyncio.sleep(seconds)
    await runner.stop()


async def test_an_hourly_job_runs_after_restarts_once_it_is_overdue(db_factory):
    runs = 0

    async def job():
        nonlocal runs
        runs += 1

    await _process(db_factory, job, 0.3)
    await _process(db_factory, job, 0.3)
    assert runs == 0

    # An hour passes across restarts that each lived less than one.
    async with db_factory() as session:
        await session.execute(
            update(JobRun)
            .where(JobRun.name == "hourly cleanup")
            .values(last_run_at=datetime.now(UTC) - timedelta(minutes=61))
        )
        await session.commit()

    await _process(db_factory, job, 0.5)
    assert runs == 1
    await _process(db_factory, job, 0.3)
    assert runs == 1
