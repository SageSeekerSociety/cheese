"""定时任务必须真的有人跑它。

这一批测试盯的不是某个任务写得对不对，而是**它到底有没有被执行**。
`taskiq` 时代的三个 cron（通知聚合收口、邮件队列出队、任务截止扫描）声明在代码里、
跟着镜像发布，但部署环境里没有任何进程去跑——失败是构造性静默的：没有报错可看，
只有缺席。一个任务过了截止时间没人被通知，房间看上去一切正常。

所以下面既测循环本身的行为（跑、出错还继续跑、间隔 0 就不跑），
也测那三个任务确实在平台开机时被排进去、而且默认是开着的。
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.background import (
    PeriodicJobs,
    PeriodicRunner,
    periodic_health,
    watch_periodic_jobs,
)
from tests.support.hang import HANG_S

pytestmark = pytest.mark.anyio


class _Runs:
    """Where the runner leases each tick. By default every tick is ours."""

    def __init__(self, *, wins: bool = True) -> None:
        self.last: dict[str, datetime] = {}
        self.claims: list[str] = []
        self.intervals: list[float] = []
        self._wins = wins

    async def claim(
        self, name: str, *, interval_s: float, who: str, now: datetime
    ) -> bool:
        self.claims.append(name)
        self.intervals.append(interval_s)
        if self._wins:
            self.last[name] = now
        return self._wins


async def test_health_names_an_unstarted_clock_only_when_this_process_owns_it():
    runner = PeriodicRunner("machine maintenance", 10, asyncio.Event().wait)
    waiting = periodic_health([runner], owns_work=False)
    assert waiting["status"] == "up"
    owned = periodic_health([runner], owns_work=True)
    assert owned["status"] == "down"
    assert owned["unhealthy"] == {"machine maintenance": "not_started"}


async def test_health_distinguishes_started_work_from_successful_work():
    started = asyncio.Event()
    complete = asyncio.Event()

    async def job():
        started.set()
        await complete.wait()

    runner = PeriodicRunner("machine maintenance", 3600, job)
    runner.start(_Runs(), datetime.now(UTC) - timedelta(hours=2))
    try:
        await asyncio.wait_for(started.wait(), HANG_S)
        reading = runner.health()
        assert reading["status"] == "running"
        assert reading["last_started_at"] is not None
        assert reading["last_succeeded_at"] is None
        complete.set()
        await asyncio.sleep(0)
        reading = runner.health()
        assert reading["last_succeeded_at"] is not None
        assert reading["running_since"] is None
    finally:
        await runner.stop()


async def test_the_watchdog_notices_a_clock_that_was_never_started(caplog):
    runner = PeriodicRunner("machine maintenance", 10, asyncio.Event().wait)
    alerted = asyncio.Event()

    class Alerted(type(caplog.handler)):
        def emit(self, record):
            super().emit(record)
            if "machine maintenance" in record.getMessage():
                alerted.set()

    import logging

    handler = Alerted()
    logger = logging.getLogger("cheesex.background")
    logger.addHandler(handler)
    watching = asyncio.create_task(
        watch_periodic_jobs(
            lambda: periodic_health([runner], owns_work=True),
            interval_s=0.005,
            grace_s=0.01,
        )
    )
    try:
        await asyncio.wait_for(alerted.wait(), HANG_S)
        assert any("not_started" in record.getMessage() for record in handler.records)
    finally:
        watching.cancel()
        await asyncio.gather(watching, return_exceptions=True)
        logger.removeHandler(handler)


async def _runs_within(
    seconds: float, interval: float, last_run: datetime | None, runs: _Runs
) -> bool:
    ran = asyncio.Event()

    async def job():
        ran.set()

    runner = PeriodicRunner("hourly", interval, job)
    runner.start(runs, last_run)
    try:
        await asyncio.wait_for(ran.wait(), timeout=seconds)
        return True
    except TimeoutError:
        return False
    finally:
        await runner.stop()


async def test_a_job_overdue_when_the_process_restarts_runs_at_once():
    runs = _Runs()
    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
    assert await _runs_within(1, 3600, two_hours_ago, runs)
    assert datetime.now(UTC) - runs.last["hourly"] < timedelta(seconds=5)


async def test_a_restart_does_not_run_a_job_before_it_is_due():
    ten_minutes_ago = datetime.now(UTC) - timedelta(minutes=10)
    assert not await _runs_within(0.2, 3600, ten_minutes_ago, _Runs())


async def test_a_job_without_a_recorded_run_waits_one_interval():
    assert not await _runs_within(0.2, 3600, None, _Runs())


@pytest.fixture
def depleted_forge_quota(monkeypatch):
    import time

    from app.core import forge_quota

    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    forge_quota.record(152342238, 500, 5000, time.time() + 600)


async def test_an_unmarked_job_keeps_running_at_ten_percent_quota(depleted_forge_quota):
    ran = asyncio.Event()

    async def job():
        ran.set()

    registry = PeriodicJobs()
    registry.register("local work", 60, job)
    runner = registry.ordered(["local work"])[0]
    assert runner._next_interval_seconds() == 60

    runner.start(_Runs(), datetime.now(UTC) - timedelta(hours=1))
    try:
        await asyncio.wait_for(ran.wait(), timeout=2)
    finally:
        await runner.stop()


async def test_an_explicit_forge_job_pauses_at_ten_percent_quota(depleted_forge_quota):
    ran = asyncio.Event()

    async def job():
        ran.set()

    registry = PeriodicJobs()
    registry.register("forge work", 60, job, forge_bound=True)
    runner = registry.ordered(["forge work"])[0]
    assert runner._next_interval_seconds() is None

    runs = _Runs()
    runner.start(runs, datetime.now(UTC) - timedelta(hours=1))
    try:
        await asyncio.sleep(0)
        assert not ran.is_set()
        assert runs.claims == []
    finally:
        await runner.stop()


async def test_interval_policy_slows_ticks_without_extending_the_lease():
    ran_twice = asyncio.Event()
    started = []

    async def job():
        started.append(asyncio.get_running_loop().time())
        if len(started) >= 2:
            ran_twice.set()

    runs = _Runs()
    runner = PeriodicRunner(
        "throttled job",
        0.01,
        job,
        interval_policy=lambda _base: 0.07,
        pause_wait=lambda: 0.07,
    )
    runner.start(runs)
    try:
        await asyncio.wait_for(ran_twice.wait(), timeout=2)
    finally:
        await runner.stop()

    assert started[1] - started[0] >= 0.06
    assert runs.intervals[:2] == [0.01, 0.01]


async def test_a_slow_tick_then_pause_keeps_base_leases_after_recovery():
    ran_twice = asyncio.Event()
    state = ["slow"]
    runs = _Runs()

    def policy(base):
        if state[0] == "paused":
            return None
        return 0.3 if state[0] == "slow" else base

    def resume_wait():
        state[0] = "recovered"
        return 0.1

    async def job():
        if len(runs.claims) == 1:
            state[0] = "paused"
        else:
            ran_twice.set()

    runner = PeriodicRunner(
        "slow then paused",
        0.01,
        job,
        interval_policy=policy,
        pause_wait=resume_wait,
    )
    runner.start(runs, datetime.now(UTC) - timedelta(seconds=1))
    try:
        await asyncio.wait_for(ran_twice.wait(), timeout=2)
    finally:
        await runner.stop()

    assert state[0] == "recovered"
    assert runs.intervals == [0.01, 0.01]


async def test_a_paused_job_does_not_run_again_before_quota_recovers():
    loop = asyncio.get_running_loop()
    resume_at = [0.0]
    ran_twice = asyncio.Event()
    started = []

    def interval_policy(base):
        return None if loop.time() < resume_at[0] else base

    async def job():
        started.append(loop.time())
        if len(started) == 1:
            resume_at[0] = loop.time() + 0.12
        else:
            ran_twice.set()

    runner = PeriodicRunner(
        "paused job",
        0.01,
        job,
        interval_policy=interval_policy,
        pause_wait=lambda: max(0.001, resume_at[0] - loop.time()),
    )
    runner.start(_Runs())
    try:
        await asyncio.wait_for(ran_twice.wait(), timeout=2)
    finally:
        await runner.stop()

    assert started[1] >= resume_at[0]


async def test_starting_while_paused_waits_for_reset_even_when_overdue():
    loop = asyncio.get_running_loop()
    resume_at = loop.time() + 0.12
    ran = asyncio.Event()
    started = []

    def interval_policy(base):
        return base if loop.time() >= resume_at else None

    async def job():
        started.append(loop.time())
        ran.set()

    runs = _Runs()
    runner = PeriodicRunner(
        "paused on startup",
        0.01,
        job,
        interval_policy=interval_policy,
        pause_wait=lambda: max(0.001, resume_at - loop.time()),
    )
    runner.start(runs, datetime.now(UTC) - timedelta(seconds=200))
    try:
        await asyncio.wait_for(ran.wait(), timeout=2)
    finally:
        await runner.stop()

    assert started[0] >= resume_at
    assert runs.intervals == [0.01]


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
    runner.start(_Runs())
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
    runner.start(_Runs())
    try:
        await asyncio.wait_for(recovered.wait(), timeout=2)
    finally:
        await runner.stop()


async def test_a_cancellation_raised_inside_a_job_does_not_end_the_loop():
    """A job that cancels something of its own raises CancelledError without
    anyone having asked the runner to stop. Ending the loop there would take the
    job out for the life of the process, silently."""
    cycles = 0

    async def job() -> None:
        nonlocal cycles
        cycles += 1
        if cycles == 1:
            raise asyncio.CancelledError

    runner = PeriodicRunner(name="cancels-itself", interval_seconds=0.01, job=job)
    runner.start(_Runs())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if cycles >= 3:
            break
    await runner.stop()

    assert cycles >= 3, "the loop stopped at the cancellation raised inside the job"


async def test_interval_zero_means_this_box_does_not_run_it():
    ran = False

    async def job():
        nonlocal ran
        ran = True

    runner = PeriodicRunner("disabled job", 0, job)
    runner.start(_Runs())
    await asyncio.sleep(0.05)
    await runner.stop()
    assert not ran


async def test_stopping_ends_the_loop():
    calls = 0
    ran = asyncio.Event()

    async def job():
        nonlocal calls
        calls += 1
        ran.set()

    runner = PeriodicRunner("test job", 0.01, job)
    runner.start(_Runs())
    # Stopped once it has run, however long a loaded machine takes to get there.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ran.wait(), timeout=HANG_S)
    await runner.stop()

    ran_by_stop = calls
    assert ran_by_stop > 0, "the loop never ran, so stopping proves nothing"
    await asyncio.sleep(0.05)
    assert calls == ran_by_stop


async def test_a_tick_another_process_holds_is_skipped_quietly():
    """每个任务、每一拍一个租约：租约不是本进程的，这一拍就不跑。

    这是任务能离开全局锁的前提 —— 每个进程都排它，靠租约保证一拍只有一个人跑。
    输掉这一拍既不是失败也不是要报的事：不出一行日志，等下一拍。
    """
    calls = 0
    runs = _Runs(wins=False)

    async def job():
        nonlocal calls
        calls += 1

    runner = PeriodicRunner("leased elsewhere", 0.01, job)
    runner.start(runs)
    await asyncio.sleep(0.3)
    await runner.stop()

    assert calls == 0, "a tick that another process held was run anyway"
    assert len(runs.claims) >= 2, "the loop stopped asking for ticks"


async def test_a_tick_that_cannot_be_leased_still_runs():
    """领不到租约（数据库抖了一下）不能变成「所有定时任务一起停摆」。

    租约是用来把两拍分开的，不是用来决定今天跑不跑的：领不到还要照跑，并且要
    大声 —— 否则一次连接故障就会静默地让每一个计划任务停下，包括那些本该发现
    这件事的任务。
    """

    class _Broken:
        async def claim(self, name, *, interval_s, who, now):
            raise RuntimeError("数据库连接没了")

    ran = asyncio.Event()

    async def job():
        ran.set()

    runner = PeriodicRunner("unleasable job", 0.01, job)
    runner.start(_Broken())
    try:
        await asyncio.wait_for(ran.wait(), timeout=2)
    finally:
        await runner.stop()


def test_a_job_is_the_lock_holder_s_until_it_is_moved_out():
    """`owner_only` 的默认值就是今天全部 29 个任务的样子：由持锁进程跑。

    这条开关存在的理由是**一个个搬出去**，所以默认必须是「还在锁里」；默认反过来
    会让每一个任务在它自己那一改动里悄悄离开锁。
    """

    async def job(): ...

    assert PeriodicRunner("by default", 60, job).owner_only is True
    assert PeriodicRunner("moved out", 60, job, owner_only=False).owner_only is False


# --- 哪些任务真的会被跑 -------------------------------------------------------


def _jobs():
    from app.periodic_jobs import periodic_jobs

    async def _noop(*_args, **_kwargs):
        return None

    return periodic_jobs(
        chat=SimpleNamespace(
            compute=SimpleNamespace(working_conversations=set),
            sweep_memory_dreams=_noop,
            session_factory=lambda: None,
        ),
        machines=_noop,
        sandboxes=_noop,
        compute=_noop,
        sessions=lambda: None,
        get_runner=lambda: SimpleNamespace(
            resume_lost_messages=_noop,
            submit=lambda *args, **kwargs: None,
        ),
    )


@pytest.mark.parametrize(
    ("name", "interval_setting"),
    [
        ("notification email drain", "notification_email_drain_interval_s"),
        ("task deadline sweep", "task_deadline_sweep_interval_s"),
        ("chat progress reminder", "chat_progress_check_interval_s"),
        # 记忆整理（dream）：这条钟是它唯一的入口，而它管的是**所有人共看的那棵
        # 树**——没跑起来不会有人报错，只会有一棵越来越乱的记忆。
        ("memory dream", "memory_dream_sweep_interval_s"),
    ],
)
def test_the_jobs_nobody_was_running_are_scheduled(name, interval_setting):
    """These had no runner in any deployed image, and each absence is
    invisible: an email queue with no consumer, a deadline nobody checks.

    Being on the list is half of it. A default interval of 0 would put them
    right back where they were — registered, deployed, and run by nobody — so
    the SHIPPED default is asserted rather than whatever this test process has
    (the harness turns some of them off; see tests/conftest.py)."""
    from app.core.config import Settings

    assert any(j.name == name for j in _jobs()), (
        f"{name!r} is not among the platform's periodic jobs"
    )
    default = Settings.model_fields[interval_setting].default
    assert default > 0, (
        f"{interval_setting} ships as {default!r} — a deployment that changes "
        f"nothing still never runs {name!r}"
    )


def test_the_timed_delivery_alarm_is_scheduled():
    """一个参与者设下的闹钟，到点得有人递（结论 17）。

    这一条的失败样子和这个文件开头那三个一模一样：`timed_deliveries` 写进去了、
    部署了，而没有任何一个循环去扫它，于是那张表成了一份没人读的愿望清单 —— 没有
    报错可看，只有缺席。它的间隔写死在列表里，不是一个设置，所以这里只问它在不在。
    """
    assert any(job.name == "timed deliveries" for job in _jobs())


def test_idle_cloud_sandboxes_are_put_to_sleep_on_a_clock():
    """Nothing else stops an idle sandbox or archives a home: without this
    job the pool's slots stay taken and its hosts are never released."""
    assert any(job.name == "cloud sandbox lifecycle" for job in _jobs())


def test_cloud_compute_is_metered_and_charged_on_a_clock():
    """Nothing else opens, closes or charges a sandbox's runs: without this
    job cloud compute runs free."""
    assert any(job.name == "cloud compute metering" for job in _jobs())


def test_forge_accounts_left_by_failed_creations_are_swept():
    assert any(job.name == "forge orphan account sweep" for job in _jobs())


@pytest.mark.parametrize(
    ("remaining", "floor"), [(4000, 0), (2500, 180), (1100, 300), (999, None)]
)
def test_only_the_github_jobs_use_the_quota_interval_policy(
    monkeypatch, remaining, floor
):
    import time

    from app.core import forge_quota
    from app.core.config import settings

    github_jobs = {
        "pr poll",
        "task pr poll",
        "task closing",
        "draft pr sweep",
        "ci activity sweep",
    }
    # Enable configured clocks so disabled test settings cannot hide a missing opt-in.
    for field in type(settings).model_fields:
        if field.endswith("_interval_s"):
            monkeypatch.setattr(settings, field, 60)
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    forge_quota.record(152342238, remaining, 5000, time.time() + 600)

    jobs = _jobs()
    assert github_jobs <= {job.name for job in jobs}
    for job in jobs:
        if job.name not in github_jobs or job.interval_seconds <= 0:
            assert job._next_interval_seconds() == job.interval_seconds, job.name
            continue
        expected = None if floor is None else max(job.interval_seconds, floor)
        assert job._next_interval_seconds() == expected, job.name
        if floor is None:
            assert 590 < job._pause_wait_seconds() <= 600, job.name


def test_every_job_is_named_once():
    names = [job.name for job in _jobs()]
    assert len(names) == len(set(names)), f"duplicate job names: {names}"


async def test_existing_repository_subscriptions_are_reconciled(monkeypatch):
    from unittest.mock import AsyncMock

    reconcile = AsyncMock(return_value={"configured": 0, "failed": 0})
    monkeypatch.setattr(
        "app.domain.project.forge.reconcile_repository_webhooks", reconcile
    )
    job = next(job for job in _jobs() if job.name == "forge event subscriptions")
    assert job.interval_seconds > 0
    await job._job()
    reconcile.assert_awaited_once()


async def test_a_job_woken_between_ticks_runs_at_once_each_time():
    """Something that should not wait an hour for the clock wakes the job, as
    often as it happens."""
    woken = asyncio.Event()
    ran = asyncio.Queue()

    async def wake(seconds: float) -> bool:
        try:
            await asyncio.wait_for(woken.wait(), seconds)
        except TimeoutError:
            return False
        woken.clear()
        return True

    async def job():
        await ran.put(datetime.now(UTC))

    runner = PeriodicRunner("assistant routines", 3600, job, woken_by=wake)
    runs = _Runs()
    runner.start(runs, datetime.now(UTC))
    try:
        await asyncio.sleep(0)
        assert ran.empty()
        woken.set()
        await asyncio.wait_for(ran.get(), HANG_S)
        woken.set()
        await asyncio.wait_for(ran.get(), HANG_S)
        assert runs.claims == []
    finally:
        await runner.stop()


def test_a_job_that_can_be_woken_runs_on_the_lock_holder_only():
    async def never(seconds: float) -> bool:
        return False

    with pytest.raises(ValueError):
        PeriodicRunner(
            "assistant routines", 30, asyncio.sleep, owner_only=False, woken_by=never
        )
