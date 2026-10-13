"""Application composition for every periodic job.

The manifest is deliberately names-only and remains in deployment order. Each
owning domain supplies its interval and callback through an explicit registrar;
this module supplies the runtime collaborators and verifies the complete set.
"""

import uuid
from collections.abc import Callable
from datetime import datetime

from app.core.background import PeriodicJobs, PeriodicRunner
from app.core.db import SessionFactory
from app.domain import backend_log
from app.domain.agent import periodic as agent_periodic
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner, addressed_to_agent
from app.domain.block.services import last_block_at
from app.domain.delivery import periodic as delivery_periodic
from app.domain.docs_site import periodic as docs_periodic
from app.domain.external_problem import periodic as external_problem_periodic
from app.domain.machine import periodic as machine_periodic
from app.domain.notification import periodic as notification_periodic
from app.domain.project import periodic as project_periodic
from app.domain.ratchet import periodic as ratchet_periodic
from app.domain.review import periodic as review_periodic
from app.domain.routine import periodic as routine_periodic
from app.domain.run_record import periodic as run_record_periodic
from app.domain.task import periodic as task_periodic
from app.domain.task.external_roster import student_roster
from app.domain.task_quiet import periodic as task_quiet_periodic
from app.domain.topic_membership.services import addressable_seat
from app.domain.usage import periodic as usage_periodic

PERIODIC_JOB_NAMES = (
    "forge event subscriptions",
    "forge credential cache cleanup",
    "forge orphan account sweep",
    "quiet task reminder",
    "pr poll",
    "task pr poll",
    "landing watch",
    "draft pr sweep",
    "orphan sweep",
    "closed task stop",
    "queued message sweep",
    "chat progress reminder",
    "checklist reminder",
    "gate sweep",
    "cloud host pool sweep",
    "cloud sandbox lifecycle",
    "cloud compute metering",
    "cloud warm pool",
    "subscription usage ingest",
    "backend error flush",
    "run record expiry",
    "notification email drain",
    "notification push drain",
    "notification digest",
    "delivery resend",
    "docs question retention",
    "timed deliveries",
    "overdue deliveries",
    "routines",
    "task deadline sweep",
    "memory dream",
    "ratchet ingest",
    "external problem sync",
)


async def _nudge_gate(
    chat: ChatService,
    sessions: SessionFactory,
    runner: AgentWorkRunner,
    topic_id: uuid.UUID,
    content: str,
    event: str,
    meta: dict,
) -> None:
    seat = await addressable_seat(sessions, topic_id)
    runner.submit(
        chat,
        topic_id,
        author="system",
        content=content,
        addressed=addressed_to_agent(seat),
        nudge_event=event,
        nudge_meta=meta,
    )


def gate_nudge_factory(
    *,
    chat: ChatService,
    sessions: SessionFactory,
    get_runner: Callable[[], AgentWorkRunner],
) -> Callable[[], review_periodic.GateNudge]:
    """Bind app-owned room addressing without making review import it."""

    def factory() -> review_periodic.GateNudge:
        runner = get_runner()
        return lambda topic_id, content, event, meta: _nudge_gate(
            chat, sessions, runner, topic_id, content, event, meta
        )

    return factory


def periodic_jobs(
    *,
    chat: ChatService,
    machines: machine_periodic.Sweeper,
    sandboxes: machine_periodic.Sweeper,
    compute: machine_periodic.Sweeper,
    sessions: SessionFactory,
    get_runner: Callable[[], AgentWorkRunner],
) -> list[PeriodicRunner]:
    """Build one checked, ordered set without importing domains from core."""
    jobs = PeriodicJobs()
    gate_nudges = gate_nudge_factory(
        chat=chat, sessions=chat.session_factory, get_runner=get_runner
    )

    async def last_activity(topic_ids: set[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        return await last_block_at(chat.session_factory, topic_ids)

    project_periodic.register_periodic_jobs(jobs, sessions=sessions)
    agent_periodic.register_periodic_jobs(
        jobs,
        chat=chat,
        sessions=sessions,
        get_runner=get_runner,
        last_activity=last_activity,
    )
    task_quiet_periodic.register_periodic_jobs(jobs, sessions=sessions)
    review_periodic.register_periodic_jobs(
        jobs,
        chat=chat,
        gate_sessions=chat.session_factory,
        gate_nudge_factory=gate_nudges,
    )
    machine_periodic.register_periodic_jobs(
        jobs,
        machines=machines,
        sandboxes=sandboxes,
        compute=compute,
        sessions=sessions,
    )
    usage_periodic.register_periodic_jobs(jobs, sessions=sessions)
    backend_log.register_periodic_jobs(jobs)
    run_record_periodic.register_periodic_jobs(jobs, sessions=sessions)
    notification_periodic.register_periodic_jobs(jobs, sessions=sessions)
    delivery_periodic.register_periodic_jobs(
        jobs, sessions=sessions, chat=chat, get_runner=get_runner
    )
    docs_periodic.register_periodic_jobs(jobs, sessions=sessions)
    routine_periodic.register_periodic_jobs(
        jobs, sessions=sessions, chat=chat, get_runner=get_runner
    )
    task_periodic.register_periodic_jobs(jobs, sessions=sessions)
    # 名单由这里递给它：外站那个域不认识题目域和用户域，这一份是两者之间那条边。
    external_problem_periodic.register_periodic_jobs(
        jobs, sessions=sessions, student_roster=student_roster
    )
    ratchet_periodic.register_periodic_jobs(jobs, sessions=sessions)
    return jobs.ordered(PERIODIC_JOB_NAMES)


__all__ = ["PERIODIC_JOB_NAMES", "gate_nudge_factory", "periodic_jobs"]
