"""PR observation and abandoned-gate recovery owned by review."""

import uuid
from collections.abc import Awaitable, Callable

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.agent.chat import ChatService
from app.domain.review import gate_sweep, pr_poll
from app.domain.review.ports import Announce

GateNudge = Callable[[uuid.UUID, str, str, dict], Awaitable[None]]
GateNudgeFactory = Callable[[], GateNudge]


async def sweep_abandoned_gates(
    sessions: SessionFactory, *, nudge_factory: GateNudgeFactory
) -> dict:
    """One nudge binding per sweep, shared with startup recovery.

    The application resolves its runner and room-addressing dependencies when
    the sweep runs. Review never imports API dependencies or another domain's
    membership adapter merely to schedule its recovery.
    """
    return await gate_sweep.sweep(sessions, nudge=nudge_factory())


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    chat: ChatService,
    announce: Announce,
    gate_sessions: SessionFactory,
    gate_nudge_factory: GateNudgeFactory,
) -> None:
    jobs.register(
        "pr poll",
        settings.accept_pr_poll_interval_s,
        lambda: pr_poll.poll_open_prs(chat, announce),
    )
    jobs.register(
        "task pr poll",
        settings.accept_pr_poll_interval_s,
        lambda: pr_poll.poll_uncarded_task_prs(chat, announce),
    )
    jobs.register(
        "landing watch",
        settings.accept_pr_poll_interval_s,
        lambda: pr_poll.watch_landings(chat, announce),
    )
    # Drafts and cards watch the same repository state on the same clock.
    jobs.register(
        "draft pr sweep",
        settings.accept_pr_poll_interval_s,
        lambda: pr_poll.open_draft_prs(chat, announce),
    )
    jobs.register(
        "gate sweep",
        settings.gate_sweep_interval_s,
        lambda: sweep_abandoned_gates(gate_sessions, nudge_factory=gate_nudge_factory),
    )
