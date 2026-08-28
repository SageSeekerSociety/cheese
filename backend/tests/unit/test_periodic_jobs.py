"""定时任务必须真的有人跑它。

这一批测试盯的不是某个任务写得对不对，而是**它到底有没有被执行**。
`taskiq` 时代的三个 cron（通知聚合收口、邮件队列出队、任务截止扫描）声明在代码里、
跟着镜像发布，但部署环境里没有任何进程去跑——失败是构造性静默的：没有报错可看，
只有缺席。一个里程碑过了截止时间没人被通知，房间看上去一切正常。

所以下面既测循环本身的行为（跑、出错还继续跑、间隔 0 就不跑），
也测那三个任务确实在平台开机时被排进去、而且默认是开着的。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.core.background import PeriodicRunner

pytestmark = pytest.mark.anyio


async def test_a_job_keeps_running_on_its_interval():
    ran = asyncio.Event()
    calls = 0

    async def job():
        nonlocal calls
        calls += 1
        if calls >= 2:
            ran.set()
        return {"did": calls}

    runner = PeriodicRunner("test job", 0.01, job)
    runner.start()
    try:
        await asyncio.wait_for(ran.wait(), timeout=2)
    finally:
        await runner.stop()
    assert calls >= 2


async def test_one_failing_cycle_does_not_end_the_loop():
    """The whole point of a maintenance loop is that it is still there tomorrow.
    A job that raises once — a lost connection, a bad row — must cost one cycle,
    not every cycle after it."""
    recovered = asyncio.Event()
    calls = 0

    async def job():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("数据库连接没了")
        recovered.set()
        return None

    runner = PeriodicRunner("flaky job", 0.01, job)
    runner.start()
    try:
        await asyncio.wait_for(recovered.wait(), timeout=2)
    finally:
        await runner.stop()


async def test_interval_zero_means_this_box_does_not_run_it():
    ran = False

    async def job():
        nonlocal ran
        ran = True

    runner = PeriodicRunner("disabled job", 0, job)
    runner.start()
    await asyncio.sleep(0.05)
    await runner.stop()
    assert not ran


async def test_stopping_ends_the_loop():
    calls = 0

    async def job():
        nonlocal calls
        calls += 1

    runner = PeriodicRunner("test job", 0.01, job)
    runner.start()
    await asyncio.sleep(0.05)
    await runner.stop()

    ran_by_stop = calls
    assert ran_by_stop > 0, "the loop never ran, so stopping proves nothing"
    await asyncio.sleep(0.05)
    assert calls == ran_by_stop


# --- 哪些任务真的会被跑 -------------------------------------------------------


def _jobs():
    from app.domain.scheduler.jobs import periodic_jobs

    async def _noop(*_args, **_kwargs):
        return None

    scheduler = SimpleNamespace(
        tick=_noop,
        reap_idle_device_screens=_noop,
        poll_open_prs=_noop,
        sync_upstreams=_noop,
        sweep_orphan_turns=_noop,
        sweep_abandoned_gates=_noop,
        sweep_conclusion_cards=_noop,
    )
    return periodic_jobs(
        scheduler=scheduler,
        machines=SimpleNamespace(sweep=_noop),
        sessions=lambda: None,
    )


@pytest.mark.parametrize(
    "name",
    [
        "notification finalize",
        "notification email drain",
        "task deadline sweep",
    ],
)
def test_the_jobs_nobody_was_running_are_scheduled(name):
    """These three had no runner in any deployed image, and each absence is
    invisible: an aggregation window that never closes, an email queue with no
    consumer, a deadline nobody checks. A default interval of 0 would put them
    right back where they were, so the interval is asserted too."""
    job = next((j for j in _jobs() if j.name == name), None)
    assert job is not None, f"{name!r} is not among the platform's periodic jobs"
    assert job.interval_seconds > 0, (
        f"{name!r} is registered but disabled by default — a deployment that "
        "does not opt in still never runs it"
    )


def test_every_job_is_named_once():
    names = [job.name for job in _jobs()]
    assert len(names) == len(set(names)), f"duplicate job names: {names}"
