"""The agent domain's clocks, with work-runner and activity readers injected."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.core.background import PeriodicJobs
from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.agent import relay_balance, spend_cap_alerts, turn_resume
from app.domain.agent.chat import ChatService
from app.domain.agent.gateway_models import roles as model_roles_store
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.session_state import running_conversations
from app.domain.agent.turn.steps.reminders import (
    QuietSessionReminders,
    StaleProgressReminder,
)
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
    # Every process serves requests that read the roles, so every process
    # refreshes them, not only the one holding the lock.
    jobs.register(
        "platform model roles refresh",
        settings.platform_model_roles_refresh_interval_s,
        lambda: model_roles_store.refresh_from(sessions),
        owner_only=False,
    )
    jobs.register(
        "model spend cap alert",
        settings.model_spend_cap_alert_interval_s,
        lambda: spend_cap_alerts.sweep(),
    )
    jobs.register(
        "relay balance check",
        settings.deepseek_balance_check_interval_s,
        lambda: relay_balance.sweep(),
    )
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
    # Both remember, per working session, when they last spoke up.
    quiet = QuietSessionReminders(chat, chat.compute)
    jobs.register(
        "chat progress reminder",
        settings.chat_progress_check_interval_s,
        lambda: quiet.silence(datetime.now(UTC)),
    )
    jobs.register(
        "checklist reminder",
        settings.chat_progress_check_interval_s,
        lambda: quiet.checklist(datetime.now(UTC)),
    )
    # One reminder for every tick: it remembers when it first saw each session
    # running and when it last asked.
    task_state_reminder = StaleProgressReminder(chat, running_conversations)
    jobs.register(
        "task state reminder",
        settings.chat_progress_check_interval_s,
        lambda: task_state_reminder(datetime.now(UTC)),
    )
    # Hands due resumes to the delivery ledger; the timed-delivery clock, next
    # in the manifest, dispatches them.
    jobs.register("turn resumes", 30, lambda: turn_resume.hand_due(sessions))
    jobs.register(
        "failed turn resume deliveries",
        30,
        lambda: turn_resume.report_failed_deliveries(sessions),
    )
    # This clock is the only entry point to memory housekeeping.
    jobs.register(
        "memory dream", settings.memory_dream_sweep_interval_s, chat.sweep_memory_dreams
    )
