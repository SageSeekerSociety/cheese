"""一次执行是怎么结束的：队友自己交回的结果，和它没交回时的判据。

`report` 是队友那一路（`cheese_routine(action=report)`），也是唯一能把一次执行判成功的那
一路；`_settle_open_runs` 是平台这一路 —— 投递失败、会话一直没读到、读到了而读进
去的那段工作失败了（runner 报的「读了，然后失败」，会话退出也在内）、读到了却一直
没有回音，都由投递台账、那份输入的回执和平台自己记下的失败判出来，不看轮次的起止。`_announce_finished`
再把结论说出去：改那条消息，并通知规则的主人。

`RoutineService` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import listing, say
from app.domain.agent.mentions import announce_mentions, project_refs_text
from app.domain.agent.turn.intake.rooms import room_roster
from app.domain.block.models import Block
from app.domain.delivery.models import Delivery, NativeInput
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


async def _failure_said(session: AsyncSession, work_id: uuid.UUID) -> str | None:
    """What the room was told when the work ``work_id`` failed or timed out, if
    anything: the words a person reads, beside the runner's failure kind."""
    return await session.scalar(
        select(Block.content)
        .where(
            Block.turn_id == work_id,
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
        # The run's input as its session read it: the echo is when the run
        # started, and the work it was read into is the run's.
        read = await session.scalar(
            select(NativeInput)
            .where(
                NativeInput.delivery_id == delivery.id,
                NativeInput.echoed_at.is_not(None),
            )
            .order_by(NativeInput.echoed_at.desc())
            .limit(1)
        )
        if read is not None and read.echoed_at is not None:
            work = read.execution_work_id or read.work_id
            if run.turn_id is None:
                run.turn_id = work
            if run.status == RunStatus.queued.value:
                run.status = RunStatus.running.value
                run.started_at = read.echoed_at
            if read.failed_at is not None:
                # Read, and the work it was read into failed — the session
                # exiting included — without a report.
                run.status = RunStatus.failed.value
                run.error = "这次没做完：" + (
                    await _failure_said(session, work) or read.failure or "未知错误"
                )
                run.finished_at = read.failed_at
            elif stamp - read.echoed_at > REPORT_TIMEOUT:
                run.status = RunStatus.failed.value
                run.error = NO_REPORT
                run.finished_at = stamp
            continue
        # Never read, and the platform said why it could not hand it over
        # (the machine was not there): the run fails now, with that reason.
        refused = (
            await _failure_said(session, delivery.attempt_id)
            if delivery.attempt_id
            else None
        )
        if refused:
            run.status = RunStatus.failed.value
            run.error = "这次没能开始：" + refused
            run.finished_at = stamp
            continue
        if stamp - run.created_at > START_TIMEOUT:
            run.status = RunStatus.failed.value
            reason = delivery.last_error or "环境没有开始运行：设备离线，或者排队太久"
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
            if message.content:
                # The teammate's words reach the room here, not through
                # chat_send, so they get what chat_send gives an agent's
                # message: a plain "@handle" becomes the mention it says it
                # is, and whoever it names is told. In a private chat nobody
                # outside it is (`room_roster`).
                message.content = await project_refs_text(
                    session, routine.project_id, topic.id, message.content
                )
                await announce_mentions(
                    session,
                    topic,
                    message,
                    routine.agent_handle,
                    await room_roster(session, routine.project_id, topic),
                    flag_unresolved=True,
                )
            changed.append(message)
        await ProjectNotificationService(session).create(
            project_id=routine.project_id,
            level=NotificationLevel.light,
            kind=NotificationType.CHANGE_ALERT,
            title=f"例行事务「{routine.title}」{verdict}",
            body=body,
            target_handle=routine.owner_handle,
            conversation_id=routine.topic_id,
            payload={"routine_id": str(routine.id), "routine_run_id": str(run.id)},
        )
    return changed
