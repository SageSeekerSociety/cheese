"""什么时候该响：到点的按时刻挑，事件类的按游标挑。

两边都按房间状态过滤 —— 归档了的房间不响，它恢复之后错过的那一次记为跳过。
事件那一路带一个每小时的次数上限，防止规则链式触发自己。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.library import listing as library_listing
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
from app.domain.routine.service._shared import (
    EVENT_RUNS_PER_HOUR,
    EVENT_TRIGGERS,
    MISSED_GRACE,
    now,
)
from app.domain.routine.service.firing import _fire
from app.domain.topic.models import Topic, TopicStatus


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
