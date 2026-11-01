"""把一条规则变成一次执行：执行行、主线上的那条消息、交给队友的提示词。

一行 `RoutineRun` 和它那条消息、支线的开口行、那一笔投递，在同一个事务里写好；
送不送得到是投递台账的事。重复的执行键由 `uq_routine_occurrence` 挡掉，所以这里
返回 `None` 就是「这一次已经排过了」。

`run_prompt` 是交给队友的那段话本身，它和 `TRIGGER_LABELS` 一起定义「这是哪种活」。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Uuid, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say, with_keys
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.agent import instance_for_seat, record_agent
from app.domain.delivery_events import DeliveryEvent
from app.domain.event_author import AuthorType
from app.domain.feedback import triage as feedback_triage
from app.domain.notice_meta import (
    EVENT_ROUTINE_RUN,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.notification.models import NotificationType
from app.domain.routine.models import (
    Routine,
    RoutineRun,
    RoutineTrigger,
    RunStatus,
)
from app.domain.routine.service._shared import (
    NO_FEEDBACK_WAITING,
    TRIGGER_LABELS,
    now,
)
from app.domain.thread.services import answered_in

_OPEN = (RunStatus.queued.value, RunStatus.running.value)


def run_prompt(routine: Routine, run: RoutineRun) -> str:
    lines = [
        f"【{TRIGGER_LABELS[RoutineTrigger(routine.trigger)] if routine.trigger else '手动'}工作】{routine.title}",
        f"这是例行事务「{routine.title}」的一次执行（执行 id {run.id}）。"
        f"{run.trigger_detail}",
        "",
        f"目标：{routine.instructions}",
    ]
    if routine.note:
        lines.append(f"补充说明：{routine.note}")
    lines += [
        "用哪些资料、结果放在哪，都按这次的目标自己定；结果文件用 cheese show "
        "放进房间，文件名带上日期。",
        "",
        "做完后必须交回结果，成功失败都要交：",
        f'cheese_routine(action=report, run="{run.id}", status="succeeded" 或 "failed", '
        'summary="结果，或失败原因", outputs=["房间里的结果文件路径"])',
        "成功时的 summary 会作为你的一条消息发在频道主线上，频道里的人直接读它："
        "写清结论，可以分几点，不用在这里再发一遍。",
        "没有交回结果的一次执行会被记为失败。",
    ]
    return "\n".join(lines)


def _offered_before(routine_id: uuid.UUID):
    """Each report earlier runs of this rule handed to a teammate: when it was
    last handed out, and whether a run that has it is still going.

    A run that failed does not count: never delivered, never started, or the
    work it was read into failed. Nor does a run that ended: whatever the
    teammate did that needs no second look is on the report itself (it holds
    the claim, or the report moved on), so a report still waiting after its run
    ended was given up on and is offered again (`feedback.triage.untriaged`).
    """
    runs = (
        select(
            func.jsonb_array_elements_text(RoutineRun.feedback_offered)
            .cast(Uuid)
            .label("feedback_id"),
            RoutineRun.created_at.label("at"),
            RoutineRun.status.in_(_OPEN).label("open"),
        )
        .where(
            RoutineRun.routine_id == routine_id,
            RoutineRun.status != RunStatus.failed.value,
        )
        .subquery()
    )
    return (
        select(
            runs.c.feedback_id,
            func.max(runs.c.at).label("last_at"),
            func.bool_or(runs.c.open).label("open"),
        )
        .group_by(runs.c.feedback_id)
        .subquery()
    )


async def _fire(
    session: AsyncSession,
    routine: Routine,
    *,
    occurrence_key: str,
    detail: str,
    scheduled_for: datetime | None,
    skip_reason: str | None = None,
) -> RoutineRun | None:
    stamp = now()
    run_id = uuid.uuid4()
    inserted = await session.scalar(
        insert(RoutineRun)
        .values(
            id=run_id,
            routine_id=routine.id,
            occurrence_key=occurrence_key[:300],
            trigger_detail=detail,
            routine_revision=routine.revision,
            scheduled_for=scheduled_for,
            status=(RunStatus.skipped if skip_reason else RunStatus.queued).value,
            error=skip_reason or "",
            created_at=stamp,
            finished_at=stamp if skip_reason else None,
            outputs=[],
        )
        .on_conflict_do_nothing(constraint="uq_routine_occurrence")
        .returning(RoutineRun.id)
    )
    if inserted is None:
        return None
    run = await session.get(RoutineRun, run_id)
    assert run is not None
    if skip_reason:
        return run
    # The run's message, in the main line where the rule lives. It says
    # 「运行中」 until the run is settled, and the run happens in its 支线 (in
    # a private chat, where there are no 支线, in the chat itself).
    message = Block(
        id=uuid.uuid4(),
        project_id=routine.project_id,
        conversation_id=routine.topic_id,
        author=routine.agent_handle,
        author_type=AuthorType.participant,
        kind=BlockKind.message,
        content="",
        meta={},
        created_at=stamp,
    )
    session.add(message)
    await session.flush()
    run.message_id = message.id
    place = await answered_in(session, message)
    agent = await instance_for_seat(session, routine.project_id, routine.agent_handle)
    if agent is None or not agent.is_active:
        run.status = RunStatus.failed.value
        run.error = f"执行者 {routine.agent_handle} 已不在这个项目里"
        run.finished_at = stamp
        return run
    content = run_prompt(routine, run)
    batch_size = routine.spec.get("feedback_batch")
    if batch_size:
        batch = await feedback_triage.untriaged(
            session, batch_size, offered=_offered_before(routine.id)
        )
        if not batch:
            # Nothing waiting is not news: no turn, no message, and no daily
            # notice to the owner saying so (`notified` is what
            # `_announce_finished` reads).
            run.message_id = None
            await session.delete(message)
            run.status = RunStatus.skipped.value
            run.error = NO_FEEDBACK_WAITING
            run.finished_at = stamp
            run.notified = True
            return run
        run.feedback_offered = [str(row.id) for row in batch]
        content = f"{content}\n\n{feedback_triage.batch_prompt(batch)}"
    event_id = uuid.uuid4()
    line = say("routineRunStarted", title=routine.title)
    session.add(
        Block(
            id=event_id,
            project_id=routine.project_id,
            conversation_id=place,
            author="system",
            author_type=AuthorType.platform,
            kind=BlockKind.event,
            content=line,
            # A row, not `BlockRepository.add`: the key is recorded here.
            meta=with_keys(
                {
                    **notice(
                        EVENT_ROUTINE_RUN,
                        severity=SEVERITY_INFO,
                        who=WHO_PLATFORM,
                        detail=content,
                        detail_label=say(
                            "labelRoutineWorkFor", who=f"<@{routine.agent_handle}>"
                        ),
                    ),
                    "routine_id": str(routine.id),
                    "routine_run_id": str(run.id),
                },
                content=line,
            ),
        )
    )
    await record_agent(
        session,
        DeliveryEvent(
            id=event_id,
            type=NotificationType.ROOM_NOTICE,
            payload={
                "projectId": str(routine.project_id),
                "topicId": str(routine.topic_id),
                "eventType": EVENT_ROUTINE_RUN,
                "severity": SEVERITY_INFO,
            },
            occurred_at=stamp,
        ),
        conversation_id=place,
        instance_id=agent.id,
        content=content,
    )
    run.delivery_event_id = event_id
    return run
