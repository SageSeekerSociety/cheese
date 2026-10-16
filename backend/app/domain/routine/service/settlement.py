"""一次执行是怎么结束的：队友自己交回的结果，和它没交回时的判据。

`report` 是队友那一路，也是唯一能改终态的那一路；`_settle_open_runs` 是平台这一
路 —— 投递失败、这一轮压根没开始、开始了却没有回音，都由投递台账和轮次记录判
出来。`_announce_finished` 再把结论说出去：改那条消息，并通知规则的主人。

`RoutineService` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import listing, say
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block
from app.domain.delivery.models import Delivery
from app.domain.notice_meta import (
    EVENT_TURN_FAILED,
    EVENT_TURN_TIMEOUT,
)
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.routine.models import (
    TERMINAL_RUN_STATUSES,
    Routine,
    RoutineRun,
    RunStatus,
)
from app.domain.routine.service._shared import (
    NO_REPORT,
    REPORT_TIMEOUT,
    START_TIMEOUT,
    now,
)
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:
    from app.domain.routine import service as pkg


async def report(
    svc: pkg.RoutineService,
    run: RoutineRun,
    *,
    by: str,
    status: str,
    summary: str,
    outputs: list[str],
) -> RoutineRun:
    routine = await svc.get(run.routine_id)
    if by != routine.agent_handle:
        raise ForbiddenError(say("routineOnlyRunnerReports"))
    if status not in (RunStatus.succeeded, RunStatus.failed):
        raise ValidationError(say("routineStatusInvalid"))
    late = run.status == RunStatus.failed and run.error == NO_REPORT
    if run.status in TERMINAL_RUN_STATUSES and not late:
        raise ValidationError(say("routineRunEnded"))
    if status == RunStatus.failed and not summary.strip():
        raise ValidationError(say("routineFailureNeedsReason"))
    run.status = status
    run.summary = summary.strip()
    run.outputs = [str(p) for p in outputs][:50]
    if status == RunStatus.failed:
        run.error = run.summary
    run.finished_at = now()
    run.started_at = run.started_at or run.finished_at
    if late:
        # The owner was told it failed; tell them how it actually went.
        run.notified = False
        if status == RunStatus.succeeded:
            run.error = ""
    await svc._session.flush()
    return run


async def _turn_failure(session: AsyncSession, turn_id: uuid.UUID) -> str | None:
    """What the room was told when this turn failed or timed out, if anything."""
    return await session.scalar(
        select(Block.content)
        .where(
            Block.turn_id == turn_id,
            Block.meta["event_type"]
            .as_string()
            .in_((EVENT_TURN_FAILED, EVENT_TURN_TIMEOUT)),
        )
        .order_by(Block.created_at.desc())
        .limit(1)
    )


async def _settle_open_runs(session: AsyncSession) -> None:
    stamp = now()
    runs = list(
        await session.scalars(
            select(RoutineRun)
            .where(RoutineRun.status.in_((RunStatus.queued, RunStatus.running)))
            .with_for_update(skip_locked=True)
        )
    )
    for run in runs:
        delivery = (
            await session.scalar(
                select(Delivery).where(Delivery.event_id == run.delivery_event_id)
            )
            if run.delivery_event_id
            else None
        )
        if delivery is None:
            continue
        if delivery.state in ("failed",):
            run.status = RunStatus.failed.value
            run.error = f"没能交给 AI 队友：{delivery.last_error or '投递失败'}"
            run.finished_at = stamp
            continue
        turn = (
            await session.get(AgentTurn, delivery.attempt_id)
            if delivery.attempt_id
            else None
        )
        if turn is not None and run.turn_id is None:
            run.turn_id = turn.id
        if (
            turn is not None
            and turn.delivered_at is None
            and turn.stopped_at is not None
        ):
            run.status = RunStatus.failed.value
            run.error = "这一轮没能开始：" + (
                await _turn_failure(session, turn.id)
                or delivery.last_error
                or "执行环境没有接住这次工作"
            )
            run.finished_at = turn.stopped_at
            continue
        if turn is not None and turn.delivered_at is not None:
            if run.status == RunStatus.queued.value:
                run.status = RunStatus.running.value
                run.started_at = turn.delivered_at
            failure = await _turn_failure(session, turn.id)
            if failure:
                run.status = RunStatus.failed.value
                run.error = "这一轮出错了：" + failure
                run.finished_at = turn.stopped_at or stamp
            elif stamp - turn.delivered_at > REPORT_TIMEOUT:
                run.status = RunStatus.failed.value
                run.error = NO_REPORT
                run.finished_at = stamp
            continue
        if stamp - run.created_at > START_TIMEOUT:
            run.status = RunStatus.failed.value
            reason = delivery.last_error or "执行环境没有接单（设备离线或排队过久）"
            run.error = (
                f"{int(START_TIMEOUT.total_seconds() // 3600)} 小时内没有开始：{reason}"
            )
            run.finished_at = stamp


async def _announce_finished(session: AsyncSession) -> list[Block]:
    """Tell how each settled run went: its message in the main line, and its
    owner. The messages returned changed; the caller publishes them once
    this commits."""

    rows = list(
        await session.execute(
            select(RoutineRun, Routine)
            .join(Routine, Routine.id == RoutineRun.routine_id)
            .where(
                RoutineRun.notified.is_(False),
                RoutineRun.status.in_(
                    (RunStatus.succeeded, RunStatus.failed, RunStatus.skipped)
                ),
            )
            .with_for_update(of=RoutineRun, skip_locked=True)
        )
    )
    changed: list[Block] = []
    for run, routine in rows:
        run.notified = True
        topic = await session.get(Topic, routine.topic_id)
        if topic is None or topic.status == TopicStatus.archived:
            continue
        ok_ = run.status == RunStatus.succeeded.value
        verdict = {
            "succeeded": say("routineSucceeded"),
            "failed": say("routineFailed"),
            "skipped": say("routineSkipped"),
        }[run.status]
        said = run.summary if ok_ else (run.error or run.summary)
        body = (
            say("routineResultWithFiles", body=said, files=listing(run.outputs))
            if run.outputs
            else said
        )
        message = await session.get(Block, run.message_id) if run.message_id else None
        if message is not None:
            # What the teammate handed back is its message; a run that did not
            # get that far says nothing in the teammate's name, and its reason
            # is the platform's, drawn beside the message. Either way it is
            # news when it is settled, so it moves to that moment.
            message.content = run.summary if ok_ else ""
            message.created_at = now()
            changed.append(message)
        await ProjectNotificationService(session).create(
            project_id=routine.project_id,
            level=NotificationLevel.light,
            kind=NotificationType.CHANGE_ALERT,
            title=f"周期任务「{routine.title}」{verdict}",
            body=body,
            target_handle=routine.owner_handle,
            conversation_id=routine.topic_id,
            payload={"routine_id": str(routine.id), "routine_run_id": str(run.id)},
        )
    return changed
