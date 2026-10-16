"""Routine firing: schedule and project-event triggers, occurrence deduplication,
and prompt assembly for the executing AI teammate."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say, with_keys
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.agent import instance_for_seat, record_agent
from app.domain.delivery_events import DeliveryEvent
from app.domain.feedback import triage as feedback_triage
from app.domain.library import listing as library_listing
from app.domain.notice_meta import (
    EVENT_ROUTINE_RUN,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.notification.models import NotificationType
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task
from app.domain.routine import schedule
from app.domain.routine.models import (
    Routine,
    RoutineRun,
    RoutineState,
    RoutineTrigger,
    RunStatus,
)
from app.domain.thread.services import answered_in
from app.domain.topic.models import Topic, TopicStatus

#: A planned moment found later than this was missed (platform down), not late.
MISSED_GRACE = timedelta(minutes=15)

#: Loop breaker: an event rule fires at most this often per hour.
EVENT_RUNS_PER_HOUR = 6

EVENT_TRIGGERS = frozenset(
    {
        RoutineTrigger.library_file_added,
        RoutineTrigger.task_closed,
        RoutineTrigger.card_accepted,
    }
)

TRIGGER_LABELS = {
    RoutineTrigger.schedule: "定时",
    RoutineTrigger.library_file_added: "资料库新增文件",
    RoutineTrigger.task_closed: "任务关闭",
    RoutineTrigger.card_accepted: "成果被采纳",
}

NO_FEEDBACK_WAITING = "没有待分诊的反馈，这一次不执行"


def now() -> datetime:
    return datetime.now(UTC)


def run_prompt(routine: Routine, run: RoutineRun, previous: str = "") -> str:
    folder = routine.output_dir or "房间根目录"
    lines = [
        f"【{TRIGGER_LABELS[RoutineTrigger(routine.trigger)]}工作】{routine.title}",
        f"这是规则「{routine.title}」的一次执行（执行 id {run.id}）。"
        f"{run.trigger_detail}",
        "",
        f"工作内容：{routine.instructions}",
        "资料范围："
        + (routine.context_scope or "按工作内容需要，只用你有权访问的项目内容"),
        f"结果保存到：{folder}（用 cheese show 放进房间，文件名带上日期）",
        "",
        "做完后必须交回结果，成功失败都要交：",
        f'cheese_routine_report(run="{run.id}", status="succeeded" 或 "failed", '
        'summary="结果，或失败原因", outputs=["房间里的结果文件路径"])',
        "成功时的 summary 会作为你的一条消息发在频道主线上，频道里的人直接读它："
        "写清结论，可以分几点，不用在这里再发一遍。",
        "没有交回结果的一次执行会被记为失败。",
    ]
    if previous:
        lines += ["", "上一次执行交回的结果：", previous]
    return "\n".join(lines)


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
    content = run_prompt(routine, run, await _previous_result(session, routine))
    batch_size = routine.spec.get("feedback_batch")
    if batch_size:
        batch = await feedback_triage.untriaged(session, batch_size)
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
                # 这一轮跑的是主人交代的活，所以它读主人的 private 记忆（组装那一
                # 轮的时候从这一笔投递上认出来）。写在这里而不是规则表上：读的那
                # 一侧（agent）不认识周期任务这个域，而这一笔它本来就在读。
                "routineOwner": routine.owner_handle,
            },
            occurred_at=stamp,
        ),
        conversation_id=place,
        instance_id=agent.id,
        content=content,
    )
    run.delivery_event_id = event_id
    return run


async def _previous_result(session: AsyncSession, routine: Routine) -> str:
    """What the rule's last successful run handed back: where this one picks
    up (last week's report, the feedback already sorted)."""
    summary = await session.scalar(
        select(RoutineRun.summary)
        .where(
            RoutineRun.routine_id == routine.id,
            RoutineRun.status == RunStatus.succeeded.value,
        )
        .order_by(RoutineRun.finished_at.desc())
        .limit(1)
    )
    return summary or ""


async def _fire_schedules(session: AsyncSession) -> int:
    stamp = now()
    rows = list(
        await session.scalars(
            select(Routine)
            .join(Topic, Topic.id == Routine.topic_id)
            .where(
                Routine.state == RoutineState.active.value,
                Routine.trigger == RoutineTrigger.schedule.value,
                Routine.next_run_at.is_not(None),
                Routine.next_run_at <= stamp,
                # An archived room (every room of an archived project is one)
                # runs nothing: its deliveries would only fail. When it comes
                # back, the missed occurrence is recorded as skipped.
                Topic.status != TopicStatus.archived,
            )
            .with_for_update(of=Routine, skip_locked=True)
        )
    )
    fired = 0
    for routine in rows:
        planned = routine.next_run_at
        assert planned is not None
        late = stamp - planned > MISSED_GRACE
        local = planned.astimezone(schedule.zone(routine.timezone))
        await _fire(
            session,
            routine,
            occurrence_key=planned.astimezone(UTC).isoformat(),
            detail=f"计划时间 {local:%Y-%m-%d %H:%M}。",
            scheduled_for=planned,
            skip_reason="错过了计划时间：平台当时没有运行，这一次不补跑"
            if late
            else None,
        )
        routine.next_run_at = schedule.next_after(
            routine.spec, routine.timezone, max(stamp, planned)
        )
        fired += 1
    return fired


async def _events_for(
    session: AsyncSession, routine: Routine, since: datetime
) -> list[tuple[str, str, datetime]]:
    """(occurrence key, human description, when) for events after `since`."""
    trigger = RoutineTrigger(routine.trigger)
    in_room = routine.spec.get("scope") == "room"
    found: list[tuple[str, str, datetime]] = []
    if trigger is RoutineTrigger.library_file_added:
        for name, when in await library_listing.added_since(
            session, routine.project_id, since
        ):
            found.append(
                (
                    f"library:{name}:{int(when.timestamp())}",
                    f"资料库新增了《{name}》。",
                    when,
                )
            )
    elif trigger is RoutineTrigger.task_closed:
        query = select(Task).where(
            Task.project_id == routine.project_id,
            Task.closed_at.is_not(None),
            Task.closed_at >= since,
        )
        if in_room:
            query = query.where(Task.room_id == routine.topic_id)
        # A task is created by a person (an agent only proposes one), so a
        # routine run never opens work that would fire it again.
        for task in await session.scalars(query):
            assert task.closed_at is not None
            found.append(
                (
                    f"task:{task.id}",
                    f"任务《{task.title}》（{task.id}）已关闭。",
                    task.closed_at,
                )
            )
    elif trigger is RoutineTrigger.card_accepted:
        query = select(AcceptCard).where(
            AcceptCard.status == AcceptStatus.accepted,
            AcceptCard.decided_at.is_not(None),
            AcceptCard.decided_at >= since,
            AcceptCard.topic_id.in_(
                select(Topic.id).where(Topic.project_id == routine.project_id)
            ),
        )
        if in_room:
            query = query.where(AcceptCard.topic_id == routine.topic_id)
        for card in await session.scalars(query):
            assert card.decided_at is not None
            name = card.deliverable_name or card.change_subject or str(card.id)
            found.append(
                (
                    f"card:{card.id}",
                    f"成果《{name}》已被 {card.decided_by or '验收人'} 采纳"
                    f"（验收卡 {card.id}）。",
                    card.decided_at,
                )
            )
    found.sort(key=lambda item: item[2])
    return found


async def _fire_events(session: AsyncSession) -> int:
    rows = list(
        await session.scalars(
            select(Routine)
            .join(Topic, Topic.id == Routine.topic_id)
            .where(
                Routine.state == RoutineState.active.value,
                Routine.trigger.in_([t.value for t in EVENT_TRIGGERS]),
                Topic.status != TopicStatus.archived,
            )
            .with_for_update(of=Routine, skip_locked=True)
        )
    )
    fired = 0
    for routine in rows:
        since = routine.event_cursor or routine.created_at
        events = await _events_for(session, routine, since)
        if not events:
            continue
        recent = await session.scalar(
            select(func.count(RoutineRun.id)).where(
                RoutineRun.routine_id == routine.id,
                RoutineRun.created_at > now() - timedelta(hours=1),
                RoutineRun.status != RunStatus.skipped.value,
            )
        )
        budget = EVENT_RUNS_PER_HOUR - int(recent or 0)
        for key, detail, _when in events:
            run = await _fire(
                session,
                routine,
                occurrence_key=key,
                detail=f"触发事件：{detail}",
                scheduled_for=None,
                skip_reason=None
                if budget > 0
                else f"一小时内已触发 {EVENT_RUNS_PER_HOUR} 次，"
                "这一次不执行（防止循环触发）",
            )
            if run is not None and run.status != RunStatus.skipped.value:
                budget -= 1
                fired += 1
        routine.event_cursor = max(when for _key, _detail, when in events)
    return fired
