"""Every periodic job the platform runs, in one list.

The failure this file exists to prevent is not a bug inside any one job. It is a
job that ships with nothing to run it: the code is written, reviewed, imported
and deployed, and then simply never executes. That failure is silent by
construction — there is no error to read, only an absence — and the thing it
takes away is exactly the thing nobody is watching for. A deadline passes and
nobody is told. A queue fills and no mail leaves. The room looks normal.

So the list is here rather than spread across the modules that each job belongs
to, and `lifespan` starts what this returns. One place to read means one place
to notice a job that is declared and never named here.

Each entry's interval comes from settings, and 0 means "not on this box" — the
only switch a job has. A job whose interval is 0 by DEFAULT is one no deployment
runs unless it opts in; that is a decision, and it should be made on purpose.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.core.background import PeriodicRunner
from app.core.config import settings
from app.core.db import SessionFactory

if TYPE_CHECKING:
    from app.domain.scheduler.service import SchedulerService


class Sweeper(Protocol):
    """Anything with one sweep to run on a clock (the machine enroller)."""

    async def sweep(self) -> dict[str, int]: ...


def periodic_jobs(
    *,
    scheduler: "SchedulerService",
    machines: Sweeper,
    sessions: SessionFactory,
) -> list[PeriodicRunner]:
    from app.domain import backend_log
    from app.domain.machine.warm import sweep_warm_pool
    from app.domain.notification.maintenance import (
        drain_email_queue,
        finalize_expired_aggregations,
    )
    from app.domain.task.deadline_scheduler import sweep_expired_deadlines
    from app.domain.topic.retire import sweep_retired_storage
    from app.domain.usage.subscription_ingest import ingest_once

    usage_log = settings.subscription_usage_log.strip()
    return [
        PeriodicRunner(
            "scheduler tick", settings.scheduler_interval_seconds, scheduler.tick
        ),
        PeriodicRunner(
            "idle screen reap",
            settings.sandbox_reap_interval_seconds,
            lambda: scheduler.reap_idle_device_screens(settings.sandbox_idle_hours),
        ),
        # 合并态轮询 (#718): mirrors pending PR cards' merge state — PR CI
        # → merge → deploy workflow → archive.
        PeriodicRunner(
            "pr poll", settings.accept_pr_poll_interval_s, scheduler.poll_open_prs
        ),
        # 自动同步上游: keeps each linked project's base current so accepting can
        # actually push. Conflicts hand off to 芝士 the same way the manual button
        # does, and an open resolution task is reused rather than duplicated.
        PeriodicRunner(
            "upstream sync",
            settings.upstream_sync_interval_s,
            scheduler.sync_upstreams,
        ),
        # The startup sweep in `lifespan` only fires when the PROCESS restarts; a
        # turn can be killed without that (container recreate, OOM, sandbox
        # swap) and then nothing would look again. This is what keeps looking.
        PeriodicRunner(
            "orphan sweep",
            settings.orphan_sweep_interval_s,
            scheduler.sweep_orphan_turns,
        ),
        PeriodicRunner(
            "chat progress reminder",
            settings.chat_progress_check_interval_s,
            scheduler.remind_silent_turns,
        ),
        # 闸门孤儿卡扫底: the same blind spot one layer down — a gate task can die
        # under a process that keeps running, and then the card waits forever
        # (see review/gate_sweep.py's module docstring).
        PeriodicRunner(
            "gate sweep",
            settings.gate_sweep_interval_s,
            scheduler.sweep_abandoned_gates,
        ),
        # Enrolling provisioned machines is platform plumbing, so it runs on its
        # own interval rather than the AI scheduler's — see machine/runner.py.
        PeriodicRunner(
            "machine enrollment sweep",
            settings.machine_enroll_interval_seconds,
            machines.sweep,
        ),
        PeriodicRunner(
            "cloud warm pool",
            settings.machine_enroll_interval_seconds,
            lambda: sweep_warm_pool(sessions),
        ),
        # Subscription turns are metered at the proxy; this tails its log into
        # resource_usage + credits (issue #218). Off unless the log path is set.
        PeriodicRunner(
            "subscription usage ingest",
            settings.subscription_ingest_interval_s if usage_log else 0,
            lambda: ingest_once(sessions, Path(usage_log)),
        ),
        # 后端报错回房间 (issue #283): closes burst windows on a clock, so a flood
        # that stopped still reports its size instead of waiting for a
        # recurrence that a fixed bug never has.
        PeriodicRunner(
            "backend error flush",
            settings.backend_error_flush_interval_s,
            backend_log.flush_expired,
        ),
        # An aggregation window that never closes is a notification written and
        # never delivered — and aggregated notifications are what a busy room
        # produces most of.
        PeriodicRunner(
            "notification finalize",
            settings.notification_finalize_interval_s,
            lambda: finalize_expired_aggregations(sessions),
        ),
        # The only consumer of the Redis list every email notification is pushed
        # onto. Unrun, that key is not slow — it grows forever and no mail goes.
        PeriodicRunner(
            "notification email drain",
            settings.notification_email_drain_interval_s,
            lambda: drain_email_queue(sessions),
        ),
        # A deadline nobody sweeps is a promise the platform made and quietly did
        # not keep: the moment it matters is the moment nobody is looking.
        PeriodicRunner(
            "task deadline sweep",
            settings.task_deadline_sweep_interval_s,
            lambda: sweep_expired_deadlines(sessions),
        ),
        # Archive takes nothing off disk: a place's worktree here and its home
        # on the device stay for the retention so an un-archive resumes with
        # its session. This is what removes them after that — transcripts
        # stored first — and at once for places no longer in the database
        # (topic/retire.py). Disk, not data: the branch stays.
        PeriodicRunner(
            "topic storage sweep",
            settings.topic_storage_sweep_interval_s,
            lambda: sweep_retired_storage(sessions),
        ),
    ]
