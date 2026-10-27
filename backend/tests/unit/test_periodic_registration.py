"""A registered clock must call the intended work with the intended collaborators."""

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import pytest

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.domain.agent import checkout_sweep
from app.domain.agent.announce import announce
from app.domain.block.queries import turn_starts
from app.domain.delivery.agent import (
    record_task_instruction,
)
from app.domain.delivery.ledger import settle_card
from app.periodic_jobs import periodic_jobs
from tests.support.review_ports import task_teammate

pytestmark = pytest.mark.anyio

# 投递包的那几件事 review 够不着，由 `periodic_jobs` 在接线时递进去
# （`app.domain.review.periodic.register_periodic_jobs`）。哪几个钟拿到哪几个口子
# 写在这里，下面按名字比 —— 名字写错就是接错线。
#
# 前四个是模块级的函数对象，`periodic_jobs` 递的就是同一个，比的是它本身。
# `dispatch_pending` 是接线时现包的闭包，外面做不出一个相等的，只认名字；它到底
# 包住了什么，由 `test_the_draft_sweep_pump_runs_on_this_chats_factory` 盯。
_EXPECTED_PORTS = {
    "settle_card": settle_card,
    "record_instruction": record_task_instruction,
    "dispatch_pending": ANY,
    "task_teammate": task_teammate,
    "machines": checkout_sweep.sweep,
}

#: 接线时从设置里现读的口子：只有草稿扫描的窗口是这种。写成模块级常量会让它与
#: 未改过的默认值相等，于是把 `periodic.py` 改成写死 7200 也照样绿。
_LIVE_PORTS = {
    "quiet_window_s": lambda: settings.draft_pr_sweep_activity_window_s,
}


_KEYWORD_PORTS = {
    "pr poll": (
        "settle_card",
        "record_instruction",
        "task_teammate",
    ),
    "task pr poll": ("record_instruction", "task_teammate"),
    "task closing": ("record_instruction", "task_teammate", "machines"),
    "draft pr sweep": ("dispatch_pending", "record_instruction", "quiet_window_s"),
}


def _world():
    sessions = Mock(name="application sessions")
    chat_sessions = Mock(name="chat sessions")
    chat = SimpleNamespace(
        session_factory=chat_sessions,
        work_runner=Mock(name="the chat's work runner"),
        stop_work_in_closed_tasks=AsyncMock(),
        compute=SimpleNamespace(working_conversations=Mock(return_value=set())),
        sweep_memory_dreams=AsyncMock(),
    )
    runner = SimpleNamespace(
        sweep_orphans=AsyncMock(return_value=7),
        resume_lost_messages=AsyncMock(return_value=2),
        submit=Mock(),
    )
    current = [runner]
    get_runner = Mock(side_effect=lambda: current[0])
    machines, sandboxes, compute = AsyncMock(), AsyncMock(), AsyncMock()
    jobs = periodic_jobs(
        chat=chat,
        sessions=sessions,
        machines=machines,
        sandboxes=sandboxes,
        compute=compute,
        get_runner=get_runner,
    )
    return SimpleNamespace(
        jobs={job.name: job for job in jobs},
        chat=chat,
        announce=announce,
        sessions=sessions,
        chat_sessions=chat_sessions,
        runner=runner,
        current=current,
        get_runner=get_runner,
        machines=machines,
        sandboxes=sandboxes,
        compute=compute,
    )


@pytest.mark.parametrize(
    ("name", "target", "argument"),
    [
        (
            "forge event subscriptions",
            "project.forge.reconcile_repository_webhooks",
            "sessions",
        ),
        (
            "forge credential cache cleanup",
            "project.forgejo_tokens.purge_expired_tokens",
            "sessions",
        ),
        (
            "forge orphan account sweep",
            "project.forge.sweep_orphan_accounts",
            "sessions",
        ),
        ("quiet task reminder", "task_quiet.sweep.remind_quiet_tasks", "sessions"),
        (
            "task deadline reminder",
            "task_notices.sweep.remind_deadlines",
            "sessions",
        ),
        ("pr poll", "review.pr_poll.poll_open_prs", ("chat", "announce")),
        ("task pr poll", "review.pr_poll.poll_uncarded_task_prs", ("chat", "announce")),
        ("task closing", "review.pr_poll.sweep_closings", ("chat", "announce")),
        ("draft pr sweep", "review.pr_poll.open_draft_prs", ("chat", "announce")),
        ("cloud warm pool", "machine.warm.sweep_warm_pool", "sessions"),
        ("sandbox disk guard", "machine.disk_guard.sweep", "sessions"),
        ("run record expiry", "run_record.service.purge_expired", "sessions"),
        ("session record seal", "session_record.service.seal", "sessions"),
        ("session record retention", "session_record.retention.sweep", "sessions"),
        (
            "notification email drain",
            "notification.maintenance.drain_email_queue",
            "sessions",
        ),
        (
            "notification push drain",
            "notification.push_delivery.drain_push_queue",
            "sessions",
        ),
        (
            "notification digest",
            "notification.digest.run_notification_digests",
            "sessions",
        ),
        ("delivery resend", "delivery.ledger.resend_unsent_deliveries", "sessions"),
        (
            "docs question retention",
            "docs_site.assistant.purge_old_questions",
            "sessions",
        ),
        ("overdue deliveries", "delivery.overdue.report_overdue", "sessions"),
        (
            "task deadline sweep",
            "task.deadline_scheduler.sweep_expired_deadlines",
            "sessions",
        ),
        ("ci activity sweep", "ci_activity.ingest.sweep", "sessions"),
    ],
)
async def test_a_registered_clock_calls_its_domain_with_the_right_binding(
    monkeypatch, name, target, argument
):
    # Patch the work at its owning domain, after the modules have already been
    # imported: registrar imports must not trap an old function binding.
    _world()
    work = AsyncMock(return_value={"did": 1})
    monkeypatch.setattr(f"app.domain.{target}", work)
    # 一个和默认值不一样的窗口：写死 7200 的接线过不了这一条。
    monkeypatch.setattr(settings, "draft_pr_sweep_activity_window_s", 4242)
    world = _world()
    assert world.get_runner.call_count == 0, "registration ran work before a tick"
    assert await world.jobs[name]._job() == {"did": 1}
    handed = argument if isinstance(argument, tuple) else (argument,)
    ports = {
        port: (_LIVE_PORTS[port]() if port in _LIVE_PORTS else _EXPECTED_PORTS[port])
        for port in _KEYWORD_PORTS.get(name, ())
    }
    work.assert_awaited_once_with(*(getattr(world, one) for one in handed), **ports)


async def test_the_draft_sweep_hands_a_pump_bound_to_this_chat(monkeypatch):
    """`dispatch_pending` 不带参数，是因为会话工厂、房间服务和跑机器在接线时就定好
    了（`app.periodic_jobs.periodic_jobs`）。上面那条只认名字，这一条认它包住的
    就是这一个 chat。"""
    pump = AsyncMock(return_value={"dispatched": 1})
    monkeypatch.setattr("app.periodic_jobs.dispatch_pending_work", pump)
    sweep = AsyncMock(return_value={})
    monkeypatch.setattr("app.domain.review.pr_poll.open_draft_prs", sweep)
    world = _world()
    await world.jobs["draft pr sweep"]._job()
    handed = sweep.await_args.kwargs["dispatch_pending"]
    assert await handed() == {"dispatched": 1}
    pump.assert_awaited_once_with(
        world.chat_sessions, chat=world.chat, runner=world.chat.work_runner
    )


async def test_the_draft_sweep_clock_hands_its_window_down(monkeypatch):
    """`open_draft_prs` 是周期那一拍和补扫共用的那道门。

    周期递进来的窗口要原样交给扫描；补扫不递，就是「不设窗口」。少了这一句，
    周期这一拍会悄悄变回扫全部 —— 正是这个 PR 要省掉的那笔 GitHub 请求。
    """
    from app.domain.review import pr_poll

    sweep = AsyncMock(return_value={"opened": 0})
    monkeypatch.setattr(pr_poll.pr_publish, "sweep_draft_prs", sweep)
    monkeypatch.setattr(pr_poll, "deliver_dependency_notices", AsyncMock())
    chat = SimpleNamespace(session_factory=Mock(name="the chat's sessions"))
    pending = Mock(name="dispatch_pending")

    await pr_poll.open_draft_prs(
        chat,
        announce,
        dispatch_pending=pending,
        record_instruction=record_task_instruction,
        quiet_window_s=7211,
    )
    assert sweep.await_args.kwargs["quiet_window_s"] == 7211

    sweep.reset_mock()
    await pr_poll.open_draft_prs(
        chat,
        announce,
        dispatch_pending=pending,
        record_instruction=record_task_instruction,
    )
    assert sweep.await_args.kwargs["quiet_window_s"] is None


@pytest.mark.parametrize(
    ("name", "collaborator"),
    [
        ("cloud host pool sweep", "machines"),
        ("cloud sandbox lifecycle", "sandboxes"),
        ("cloud compute metering", "compute"),
    ],
)
async def test_machine_clocks_call_the_explicitly_supplied_sweeper(name, collaborator):
    world = _world()
    await world.jobs[name]._job()
    for candidate in ("machines", "sandboxes", "compute"):
        expected = 1 if candidate == collaborator else 0
        assert getattr(world, candidate).await_count == expected


@pytest.mark.parametrize(
    ("name", "method"),
    [
        ("closed task stop", "stop_work_in_closed_tasks"),
        ("memory dream", "sweep_memory_dreams"),
    ],
)
async def test_chat_clocks_call_the_supplied_chat(name, method):
    world = _world()
    await world.jobs[name]._job()
    getattr(world.chat, method).assert_awaited_once_with()


@pytest.mark.parametrize("name", ["chat progress reminder", "checklist reminder"])
async def test_reminder_clocks_ask_the_supplied_chats_sessions(name):
    world = _world()
    await world.jobs[name]._job()
    world.chat.compute.working_conversations.assert_called_once_with()


@pytest.mark.parametrize(
    ("name", "target", "handed"),
    [
        (
            "timed deliveries",
            "delivery.timer.deliver_due",
            {"machines": checkout_sweep.sweep},
        ),
        ("routines", "routine.service.sweep", {}),
    ],
)
async def test_a_dispatch_clock_resolves_the_runner_when_it_ticks(
    monkeypatch, name, target, handed
):
    work = AsyncMock(return_value={"delivered": 1})
    monkeypatch.setattr(f"app.domain.{target}", work)
    world = _world()
    replacement = object()
    world.current[0] = replacement
    await world.jobs[name]._job()
    work.assert_awaited_once_with(
        world.sessions, chat=world.chat, runner=replacement, **handed
    )
    world.get_runner.assert_called_once_with()


async def test_the_queue_sweep_resumes_on_the_runner_available_at_the_tick():
    world = _world()
    replacement = SimpleNamespace(resume_lost_messages=AsyncMock())
    world.current[0] = replacement
    await world.jobs["queued message sweep"]._job()
    replacement.resume_lost_messages.assert_awaited_once_with(
        world.chat, source="sweep"
    )
    world.runner.resume_lost_messages.assert_not_awaited()


async def test_the_orphan_sweep_reads_the_chat_activity_and_silence_limit(monkeypatch):
    activity = AsyncMock(return_value={})
    monkeypatch.setattr("app.periodic_jobs.last_block_at", activity)
    monkeypatch.setattr(settings, "turn_silence_timeout_s", 123)
    world = _world()
    replacement = SimpleNamespace(sweep_orphans=AsyncMock(return_value=3))
    world.current[0] = replacement
    assert await world.jobs["orphan sweep"]._job() == 3
    args, kwargs = replacement.sweep_orphans.call_args
    assert args == (world.chat,)
    assert kwargs["silence_s"] == 123
    topic_ids = {uuid.uuid4()}
    await kwargs["last_activity"](topic_ids)
    activity.assert_awaited_once_with(world.chat_sessions, topic_ids)
    world.runner.sweep_orphans.assert_not_awaited()


async def test_gate_recovery_binds_one_runner_per_sweep_and_addresses_the_seat(
    monkeypatch,
):
    topic_id = uuid.uuid4()
    meta = {"detail": "gate abandoned"}
    seat = AsyncMock(return_value="seat-owner")
    addressed = Mock(return_value="addressed-seat")
    monkeypatch.setattr("app.periodic_jobs.addressable_seat", seat)
    monkeypatch.setattr("app.periodic_jobs.addressed_to_agent", addressed)
    world = _world()
    replacement = SimpleNamespace(submit=Mock())
    world.current[0] = replacement

    async def recover(sessions, *, nudge):
        assert sessions is world.chat_sessions
        # A sweep binds its runner once, just as startup recovery does. A later
        # replacement must not split this sweep across two work registries.
        world.current[0] = SimpleNamespace(submit=Mock())
        await nudge(topic_id, "retry the card", "gate abandoned", meta)
        return {"condemned": [topic_id], "errors": []}

    monkeypatch.setattr("app.domain.review.gate_sweep.sweep", recover)
    result = await world.jobs["gate sweep"]._job()
    assert result["condemned"] == [topic_id]
    seat.assert_awaited_once_with(world.chat_sessions, topic_id)
    addressed.assert_called_once_with("seat-owner")
    replacement.submit.assert_called_once_with(
        world.chat,
        topic_id,
        author="system",
        content="retry the card",
        addressed="addressed-seat",
        nudge_event="gate abandoned",
        nudge_meta=meta,
    )
    world.get_runner.assert_called_once_with()


@pytest.mark.parametrize("log_path", ["", "   ", " /tmp/usage.jsonl "])
async def test_subscription_ingestion_is_disabled_or_uses_the_trimmed_path(
    monkeypatch, log_path
):
    monkeypatch.setattr(settings, "subscription_usage_log", log_path)
    monkeypatch.setattr(settings, "subscription_ingest_interval_s", 23)
    ingest = AsyncMock(return_value=5)
    monkeypatch.setattr("app.domain.usage.subscription_ingest.ingest_once", ingest)
    world = _world()
    job = world.jobs["subscription usage ingest"]
    assert job.interval_seconds == (23 if log_path.strip() else 0)
    if log_path.strip():
        assert await job._job() == 5
        # `block` 那个读是接线时递的，递的就是它本身（`app.periodic_jobs`）。
        ingest.assert_awaited_once_with(
            world.sessions, Path("/tmp/usage.jsonl"), turn_starts=turn_starts
        )
    else:
        ingest.assert_not_awaited()


async def test_the_error_clock_flushes_the_backend_error_intake(monkeypatch):
    flush = AsyncMock(return_value=4)
    monkeypatch.setattr("app.domain.backend_log.flush_expired", flush)
    world = _world()
    assert await world.jobs["backend error flush"]._job() == 4
    flush.assert_awaited_once_with()


def test_a_duplicate_registration_is_rejected_instead_of_overwriting_work():
    registry = PeriodicJobs()
    registry.register("one", 10, AsyncMock())
    with pytest.raises(ValueError, match="registered twice"):
        registry.register("one", 20, AsyncMock())


@pytest.mark.parametrize(
    ("registered", "manifest", "error"),
    [
        (["one"], ["one", "one"], "duplicate"),
        (["one"], ["one", "missing"], "missing"),
        (["one", "unlisted"], ["one"], "unlisted"),
    ],
)
def test_a_manifest_with_missing_extra_or_duplicate_jobs_is_rejected(
    registered, manifest, error
):
    registry = PeriodicJobs()
    for name in registered:
        registry.register(name, 10, AsyncMock())
    with pytest.raises(ValueError, match=error):
        registry.ordered(manifest)


def test_registrations_are_instance_owned_and_preserve_order_and_owner():
    first, second = PeriodicJobs(), PeriodicJobs()
    first.register("a", 17, AsyncMock())
    first.register("b", 29, AsyncMock(), owner_only=False)
    ordered = first.ordered(["b", "a"])
    assert [(job.name, job.interval_seconds, job.owner_only) for job in ordered] == [
        ("b", 29, False),
        ("a", 17, True),
    ]
    assert second.ordered([]) == []
    with pytest.raises(ValueError, match="missing"):
        second.ordered(["a"])


def test_every_platform_clock_keeps_its_order_interval_source_and_ownership(
    monkeypatch,
):
    fields = (
        "accept_pr_poll_interval_s",
        "orphan_sweep_interval_s",
        "closed_task_stop_interval_s",
        "queued_message_sweep_interval_s",
        "chat_progress_check_interval_s",
        "gate_sweep_interval_s",
        "machine_enroll_interval_seconds",
        "subscription_ingest_interval_s",
        "backend_error_flush_interval_s",
        "run_record_expiry_interval_s",
        "notification_email_drain_interval_s",
        "notification_push_drain_interval_s",
        "notification_digest_interval_s",
        "delivery_resend_interval_s",
        "delivery_overdue_check_interval_s",
        "task_deadline_sweep_interval_s",
        "memory_dream_sweep_interval_s",
        "sandbox_disk_check_s",
        "draft_pr_sweep_interval_s",
        "ci_activity_sweep_interval_s",
        "platform_model_roles_refresh_interval_s",
        "model_spend_cap_alert_interval_s",
        "credit_warning_interval_s",
        "deepseek_balance_check_interval_s",
    )
    for value, field in enumerate(fields, start=101):
        monkeypatch.setattr(settings, field, value)
    monkeypatch.setattr(settings, "subscription_usage_log", "/tmp/usage.jsonl")
    world = _world()
    assert [(job.name, job.interval_seconds) for job in world.jobs.values()] == [
        ("forge event subscriptions", 300),
        ("forge credential cache cleanup", 3600),
        ("forge orphan account sweep", 3600),
        ("quiet task reminder", 3600),
        ("task deadline reminder", 3600),
        ("pr poll", 101),
        ("task pr poll", 101),
        ("task closing", 101),
        ("draft pr sweep", 119),
        ("orphan sweep", 102),
        ("closed task stop", 103),
        ("queued message sweep", 104),
        ("chat progress reminder", 105),
        ("checklist reminder", 105),
        ("task state reminder", 105),
        ("gate sweep", 106),
        ("cloud host pool sweep", 107),
        ("cloud sandbox lifecycle", 107),
        ("cloud compute metering", 107),
        ("cloud warm pool", 107),
        ("sandbox disk guard", 118),
        ("subscription usage ingest", 108),
        ("backend error flush", 109),
        ("run record expiry", 110),
        ("session record seal", 60),
        ("session record retention", 600),
        ("retired site archive", 60),
        ("notification email drain", 111),
        ("notification push drain", 112),
        ("notification digest", 113),
        ("delivery resend", 114),
        ("docs question retention", 86400),
        ("turn resumes", 30),
        ("failed turn resume deliveries", 30),
        ("timed deliveries", 30),
        ("overdue deliveries", 115),
        ("routines", 30),
        ("assistant routines", 30),
        ("assistant memory tidy", 3600),
        ("task deadline sweep", 116),
        ("memory dream", 117),
        ("platform model roles refresh", 121),
        ("model spend cap alert", 122),
        ("relay balance check", 124),
        ("credit warnings", 123),
        ("ci activity sweep", 300),
    ]
    # Every process serves requests that read the platform model roles, so that
    # one refresh runs everywhere; every other clock only on the lock holder.
    assert [name for name, job in world.jobs.items() if not job.owner_only] == [
        "platform model roles refresh"
    ]
    world.get_runner.assert_not_called()


@pytest.mark.parametrize(
    ("interval", "expected"), [(0, 0), (60, 300), (300, 300), (900, 900)]
)
def test_ci_reporting_never_polls_faster_than_five_minutes(
    monkeypatch, interval, expected
):
    monkeypatch.setattr(settings, "ci_activity_sweep_interval_s", interval)
    assert _world().jobs["ci activity sweep"].interval_seconds == expected
