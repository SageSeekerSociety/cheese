"""什么时候该响：到点的按时刻挑，事件类的按游标挑。

两边都按房间状态过滤 —— 归档了的房间不响，它恢复之后错过的那一次记为跳过。
到点时上一次还没结束，这一次记为跳过，不叠着跑。事件那一路带一个每小时的次数
上限，防止规则链式触发自己；队友自己开的任务关闭也不算它的事件。

一条规则出了错只算它自己的：每条规则在自己的保存点里响，出错就退回这一条、记
下日志，同一趟的别的规则照常（否则一条坏规则每 30 秒带着整趟一起回滚）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.numbering import lock_conversations
from app.domain.library import listing as library_listing
from app.domain.library.shelf import Shelf
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
    EVENT_LOOKBACK,
    EVENT_RUNS_PER_HOUR,
    EVENT_TRIGGERS,
    MISSED_GRACE,
    OVER_EVENT_LIMIT,
    STILL_RUNNING,
    now,
)
from app.domain.routine.service.firing import _fire
from app.domain.topic.models import Topic, TopicStatus

logger = logging.getLogger(__name__)

_OPEN = (RunStatus.queued.value, RunStatus.running.value)


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
    busy = await _still_running(session, [routine.id for routine in rows])
    # Every room told in this pass, at once, before the first line
    # (`conversation.numbering`); a run's own 支线 is new and needs none.
    await lock_conversations(session, [routine.topic_id for routine in rows])
    fired = 0
    for routine in rows:
        planned = routine.next_run_at
        assert planned is not None
        late = stamp - planned > MISSED_GRACE
        local = planned.astimezone(schedule.zone(routine.timezone))
        skip = (
            "错过了计划时间：平台当时没有运行，这一次不补跑"
            if late
            else STILL_RUNNING
            if routine.id in busy
            else None
        )
        try:
            async with session.begin_nested():
                await _fire(
                    session,
                    routine,
                    occurrence_key=planned.astimezone(UTC).isoformat(),
                    detail=f"计划时间 {local:%Y-%m-%d %H:%M}。",
                    scheduled_for=planned,
                    skip_reason=skip,
                )
        except Exception:
            logger.exception("routine %s could not fire", routine.id)
        else:
            fired += 1
        # Past this moment either way: a rule that cannot fire now is not
        # tried again every sweep.
        routine.next_run_at = schedule.next_after(
            routine.spec, routine.timezone, max(stamp, planned)
        )
    return fired


async def _still_running(session: AsyncSession, ids: list) -> set:
    """The rules among ``ids`` whose last run has not ended."""
    if not ids:
        return set()
    return set(
        await session.scalars(
            select(RoutineRun.routine_id).where(
                RoutineRun.routine_id.in_(ids), RoutineRun.status.in_(_OPEN)
            )
        )
    )


async def _events_for(
    session: AsyncSession, routine: Routine, since: datetime
) -> list[tuple[str, str, datetime]]:
    """(occurrence key, human description, when) for events after `since`."""
    if not routine.trigger:
        return []
    trigger = RoutineTrigger(routine.trigger)
    in_room = routine.spec.get("scope") == "room"
    found: list[tuple[str, str, datetime]] = []
    if trigger is RoutineTrigger.library_file_added:
        for name, when in await library_listing.added_since(
            # 「资料库新增」说的是项目自己放进来的；题目的材料不算。
            session,
            Shelf.of_project(routine.project_id, with_task=False),
            since,
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
        # A teammate opens tasks too (`/teammate-tasks`): one the rule's own
        # teammate opened, say in a run, closing is not news to the rule.
        query = query.where(
            (Task.created_by.is_(None)) | (Task.created_by != routine.agent_handle)
        )
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
    # 先算每条规则的新事件，事件为空时照旧提前 continue（空趟不额外查 routine_runs）；
    # 有待触发事件的规则在进触发循环前用一条 GROUP BY 一次数完最近一小时的触发次数。
    stamp = now()
    due: list[tuple[Routine, list[tuple[str, str, datetime]]]] = []
    for routine in rows:
        since = routine.event_cursor or routine.created_at
        events = await _events_for(session, routine, since)
        if not events:
            continue
        due.append((routine, events))
    if not due:
        return 0
    # As `_fire_schedules`: every room told in this pass, at once, first.
    await lock_conversations(session, [routine.topic_id for routine, _events in due])
    ids = [routine.id for routine, _events in due]
    # One query for the hour behind every due rule: how many runs it started,
    # and whether its owner has been told of the limit already.
    hour = (
        await session.execute(
            select(
                RoutineRun.routine_id,
                func.count(RoutineRun.id).filter(
                    RoutineRun.status != RunStatus.skipped.value
                ),
                func.count(RoutineRun.id).filter(RoutineRun.error == OVER_EVENT_LIMIT),
            )
            .where(
                RoutineRun.routine_id.in_(ids),
                RoutineRun.created_at > now() - timedelta(hours=1),
            )
            .group_by(RoutineRun.routine_id)
        )
    ).all()
    recent = {routine_id: int(started) for routine_id, started, _told in hour}
    told = {routine_id for routine_id, _started, over in hour if over}
    fired = 0
    for routine, events in due:
        budget = EVENT_RUNS_PER_HOUR - recent.get(routine.id, 0)
        for key, detail, _when in events:
            try:
                async with session.begin_nested():
                    run = await _fire(
                        session,
                        routine,
                        occurrence_key=key,
                        detail=f"触发事件：{detail}",
                        scheduled_for=None,
                        skip_reason=None if budget > 0 else OVER_EVENT_LIMIT,
                    )
            except Exception:
                logger.exception("routine %s could not fire for %s", routine.id, key)
                continue
            if run is None:
                continue  # found again within the lookback: already fired
            if run.status != RunStatus.skipped.value:
                budget -= 1
                fired += 1
            elif run.error == OVER_EVENT_LIMIT:
                # The owner hears of the limit once an hour, not once per
                # event over it.
                run.notified = routine.id in told
                told.add(routine.id)
        # The cursor stays a little behind the clock, so an event committed
        # after this sweep with an earlier time is still found by the next.
        latest = max(when for _key, _detail, when in events)
        routine.event_cursor = max(_since(routine), min(latest, stamp - EVENT_LOOKBACK))
    return fired


def _since(routine: Routine) -> datetime:
    return routine.event_cursor or routine.created_at
