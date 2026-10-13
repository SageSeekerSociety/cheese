"""A long-interval job still runs on a backend restarted more often than it is due.

And the clock is a lease on one tick, not a note that somebody ran: a job that no
longer depends on the ownership lock (迁移顺序 3e) is scheduled on every process,
and these cases pin that exactly one of them runs each tick.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from sqlalchemy import update

from app.core.background import PeriodicRunner
from app.core.job_runs import JobRun, JobRuns

pytestmark = pytest.mark.anyio


async def _process(db_factory, job, seconds: float) -> None:
    """One backend process holding the jobs for a while, then going away."""
    runs = JobRuns(db_factory)
    last = await runs.load(["hourly cleanup"], datetime.now(UTC))
    runner = PeriodicRunner("hourly cleanup", 3600, job)
    runner.start(runs, last["hourly cleanup"])
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


async def test_two_processes_claiming_at_once_only_one_gets_the_tick(db_factory):
    """每个任务、每一拍一个租约：同时来领的两个进程只有一个拿到。

    这是任务能离开全局锁的前提（迁移顺序 3e）：搬出去的任务在每个进程上都被
    调度，一拍跑不跑只有这一条语句说了算。
    """
    first, second = JobRuns(db_factory), JobRuns(db_factory)
    now = datetime.now(UTC)
    mine, yours = await asyncio.gather(
        first.claim("hourly cleanup", interval_s=3600, who="host-a:1", now=now),
        second.claim("hourly cleanup", interval_s=3600, who="host-b:2", now=now),
    )
    assert sorted([mine, yours]) == [False, True]


async def test_a_lease_records_who_holds_it_and_until_when(db_factory):
    """这一行要说三件事：上次什么时候跑、现在谁在跑、跑权留到什么时候。

    没有后两件，读到这一行的人只知道「有人跑过」；而下一拍该不该跑，看的正是
    后来那件 —— 领跑的人还在跑的时候，别的进程不该插进来。
    """
    runs = JobRuns(db_factory)
    now = datetime.now(UTC)
    assert await runs.claim("hourly cleanup", interval_s=3600, who="host-a:7", now=now)
    async with db_factory() as session:
        row = await session.get(JobRun, "hourly cleanup")
    assert row is not None
    assert row.run_by == "host-a:7"
    assert row.run_until == now + timedelta(seconds=3600)

    # 租约还立着：另一个进程此刻领不走这一拍。
    assert not await runs.claim(
        "hourly cleanup",
        interval_s=3600,
        who="host-b:8",
        now=now + timedelta(minutes=1),
    )
    # 到期之后就是它的了。
    assert await runs.claim(
        "hourly cleanup",
        interval_s=3600,
        who="host-b:8",
        now=now + timedelta(seconds=3601),
    )
    async with db_factory() as session:
        taken_over = await session.get(JobRun, "hourly cleanup")
    assert taken_over is not None
    assert taken_over.run_by == "host-b:8"


async def test_a_job_scheduled_on_every_process_runs_once_a_tick(db_factory):
    """搬到锁外面的任务长的样子（迁移顺序 3e）：两个进程都排着它，一拍跑一次。

    两次运行之间至少隔着一个间隔：`run_until` 到点之前谁都领不走下一拍。租约
    不管用的话，两个进程会在同一拍上前后脚跑完，间隔近乎为零 —— 这正是「跑了
    几次」在慢机器上看不出来的那件事。
    """
    interval = 0.05
    ran: list[float] = []

    async def job():
        ran.append(asyncio.get_running_loop().time())

    async def process(seconds: float) -> None:
        runner = PeriodicRunner("hourly cleanup", interval, job, owner_only=False)
        runner.start(JobRuns(db_factory), None)
        await asyncio.sleep(seconds)
        await runner.stop()

    await asyncio.gather(process(1.0), process(1.0))

    assert len(ran) >= 2, "two loops on one job never ran it twice"
    closest = min(b - a for a, b in pairwise(ran))
    assert closest >= interval * 0.9, (
        f"two ticks ran {closest:.3f}s apart — inside the {interval}s lease"
    )
