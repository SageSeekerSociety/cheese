"""The agent domain's clocks, with work-runner and activity readers injected."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.project import forgejo_tokens

LastActivity = Callable[[set[uuid.UUID]], Awaitable[dict[uuid.UUID, datetime]]]


async def sweep_orphan_turns(
    chat: ChatService,
    *,
    get_runner: Callable[[], AgentWorkRunner],
    last_activity: LastActivity,
) -> int:
    """Notice dead turns even when their backend process never restarted."""
    return await get_runner().sweep_orphans(
        chat, last_activity=last_activity, silence_s=settings.turn_silence_timeout_s
    )


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    chat: ChatService,
    sessions: SessionFactory,
    get_runner: Callable[[], AgentWorkRunner],
    last_activity: LastActivity,
) -> None:
    jobs.register(
        "forge credential cache cleanup",
        3600,
        lambda: forgejo_tokens.purge_expired_tokens(sessions),
    )
    jobs.register(
        "orphan sweep",
        settings.orphan_sweep_interval_s,
        lambda: sweep_orphan_turns(
            chat, get_runner=get_runner, last_activity=last_activity
        ),
    )
    jobs.register(
        "closed task stop",
        settings.closed_task_stop_interval_s,
        lambda: chat.stop_work_in_closed_tasks(),
    )
    # Read the runner at the tick, not at registration: a missed wake-up must
    # resume on whichever runner the application currently owns.
    jobs.register(
        "queued message sweep",
        settings.queued_message_sweep_interval_s,
        lambda: get_runner().resume_lost_messages(chat, source="sweep"),
    )
    jobs.register(
        "chat progress reminder",
        settings.chat_progress_check_interval_s,
        chat.remind_silent_turns,
    )
    jobs.register(
        "checklist reminder",
        settings.chat_progress_check_interval_s,
        chat.remind_stale_checklists,
    )
    # This clock is the only entry point to memory housekeeping.
    jobs.register(
        "memory dream", settings.memory_dream_sweep_interval_s, chat.sweep_memory_dreams
    )
