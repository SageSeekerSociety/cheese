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
from app.domain.agent import checkout_sweep
from app.domain.agent import periodic as agent_periodic
from app.domain.agent.announce import announce
from app.domain.agent.chat import ChatService
from app.domain.agent.project_feed import PROJECT, tell_project
from app.domain.agent.runtime import AgentWorkRunner, addressed_to_agent
from app.domain.agent.session_state import running_conversations
from app.domain.agent_instance.adapters import task_teammate
from app.domain.block.queries import turn_starts
from app.domain.block.services import last_block_at
from app.domain.ci_activity import periodic as ci_activity_periodic
from app.domain.delivery import periodic as delivery_periodic
from app.domain.delivery.agent import (
    dispatch_pending as dispatch_pending_work,
)
from app.domain.delivery.agent import (
    record_task_instruction,
)
from app.domain.delivery.ledger import settle_card
from app.domain.docs_site import periodic as docs_periodic
from app.domain.integration import periodic as integration_periodic
from app.domain.machine import periodic as machine_periodic
from app.domain.notification import periodic as notification_periodic
from app.domain.project import periodic as project_periodic
from app.domain.review import periodic as review_periodic
from app.domain.routine import periodic as routine_periodic
from app.domain.run_record import periodic as run_record_periodic
from app.domain.session_record import periodic as session_record_periodic
from app.domain.task import periodic as task_periodic
from app.domain.task_notices import periodic as task_notices_periodic
from app.domain.task_quiet import periodic as task_quiet_periodic
from app.domain.thread import periodic as thread_periodic
from app.domain.topic_membership.services import addressable_seat
from app.domain.usage import periodic as usage_periodic

PERIODIC_JOB_NAMES = (
    "forge event subscriptions",
    "forge credential cache cleanup",
    "forge orphan account sweep",
    "quiet task reminder",
    "task deadline reminder",
    "pr poll",  # forge_bound: PR status reads
    "task pr poll",  # forge_bound: task branch/PR reads
    "task closing",  # forge_bound: a task going on reads its next branch
    "draft pr sweep",  # forge_bound: branch/compare reads and PR creation
    "orphan sweep",
    "closed task stop",
    "queued message sweep",
    "chat progress reminder",
    "checklist reminder",
    "task state reminder",
    "gate sweep",
    "cloud host pool sweep",
    "cloud sandbox lifecycle",
    "cloud compute metering",
    "cloud warm pool",
    "sandbox disk guard",
    "subscription usage ingest",
    "backend error flush",
    "run record expiry",
    "session record seal",
    "session record retention",
    "retired site archive",
    "notification email drain",
    "notification push drain",
    "notification digest",
    "delivery resend",
    "docs question retention",
    "question expiry",
    "thread recycling",
    "turn resumes",
    "failed turn resume deliveries",
    "timed deliveries",
    "overdue deliveries",
    "routines",
    "assistant routines",
    "assistant memory tidy",
    "feishu authorization renewal",
    "task deadline sweep",
    "starter code pushes",
    "memory dream",
    "platform model roles refresh",
    "model spend cap alert",
    "ci activity sweep",  # forge_bound: GitHub Actions run/job reads
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
    assistants: Callable[[], tuple[object, object]] | None = None,
) -> list[PeriodicRunner]:
    """Build one checked, ordered set without importing domains from core."""
    jobs = PeriodicJobs()
    gate_nudges = gate_nudge_factory(
        chat=chat, sessions=chat.session_factory, get_runner=get_runner
    )

    # 投递包的那几件事，review 自己够不着，由这里递给它（`review.periodic`）。
    # 泵不带参数：会话工厂、房间服务和跑机器在这里就定好了，tick 到的时候只差
    # 「放」这一个动作。
    async def dispatch_pending() -> object:
        return await dispatch_pending_work(
            chat.session_factory, chat=chat, runner=chat.work_runner
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
    task_notices_periodic.register_periodic_jobs(jobs, sessions=sessions)
    review_periodic.register_periodic_jobs(
        jobs,
        chat=chat,
        announce=announce,
        gate_sessions=chat.session_factory,
        gate_nudge_factory=gate_nudges,
        settle_card=settle_card,
        record_instruction=record_task_instruction,
        dispatch_pending=dispatch_pending,
        task_teammate=task_teammate,
        machines=checkout_sweep.sweep,
    )
    machine_periodic.register_periodic_jobs(
        jobs,
        machines=machines,
        sandboxes=sandboxes,
        compute=compute,
        sessions=sessions,
    )
    usage_periodic.register_periodic_jobs(
        jobs, sessions=sessions, turn_starts=turn_starts
    )
    backend_log.register_periodic_jobs(jobs)
    run_record_periodic.register_periodic_jobs(jobs, sessions=sessions)
    session_record_periodic.register_periodic_jobs(jobs, sessions=sessions)
    notification_periodic.register_periodic_jobs(jobs, sessions=sessions)
    delivery_periodic.register_periodic_jobs(
        jobs,
        sessions=sessions,
        chat=chat,
        get_runner=get_runner,
        machines=checkout_sweep.sweep,
    )
    docs_periodic.register_periodic_jobs(jobs, sessions=sessions)
    routine_periodic.register_periodic_jobs(
        jobs,
        sessions=sessions,
        chat=chat,
        get_runner=get_runner,
        assistants=assistants,
    )
    integration_periodic.register_periodic_jobs(jobs, sessions=sessions)

    async def starter_settled(project_id: uuid.UUID, room_id: uuid.UUID) -> None:
        await tell_project(room_id, PROJECT, project_id=project_id)

    task_periodic.register_periodic_jobs(
        jobs, sessions=sessions, starter_settled=starter_settled
    )
    ci_activity_periodic.register_periodic_jobs(jobs, sessions=sessions)
    thread_periodic.register_periodic_jobs(
        jobs, sessions=sessions, running=running_conversations
    )
    return jobs.ordered(PERIODIC_JOB_NAMES)


__all__ = ["PERIODIC_JOB_NAMES", "gate_nudge_factory", "periodic_jobs"]
