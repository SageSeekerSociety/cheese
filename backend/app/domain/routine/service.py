"""Routines: create and govern the rules, fire them, and settle each run.

Firing writes the run row, the room's event block and the agent's delivery in
one transaction; the delivery ledger then owns getting the prompt to the
teammate. A run is settled by the teammate's own report, or, when the turn ends
without one or never starts, by what the ledger and the turn record show.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.agent.models import AgentTurn
from app.domain.agent.platform_notices import (
    EVENT_ROUTINE_PROPOSED,
    EVENT_ROUTINE_RESULT,
    EVENT_ROUTINE_RUN,
    EVENT_TURN_FAILED,
    EVENT_TURN_TIMEOUT,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    WHO_CHEESE,
    WHO_PLATFORM,
    notice,
)
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.agent import dispatch_pending, instance_for_seat, record_agent
from app.domain.delivery.ledger import DeliveryEvent
from app.domain.delivery.models import Delivery
from app.domain.library import service as library
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task
from app.domain.routine import schedule
from app.domain.routine.models import (
    TERMINAL_RUN_STATUSES,
    Routine,
    RoutineRun,
    RoutineState,
    RoutineTrigger,
    RunStatus,
)
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

#: A planned moment found later than this was missed (platform down), not late.
MISSED_GRACE = timedelta(minutes=15)
#: A queued run whose turn never began is reported as not started after this.
START_TIMEOUT = timedelta(hours=2)
#: A turn that ended without a report gets this long for a late report to land.
REPORT_GRACE = timedelta(minutes=2)
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
    RoutineTrigger.task_closed: "任务完成",
    RoutineTrigger.card_accepted: "成果被采纳",
}


def now() -> datetime:
    return datetime.now(UTC)


def _clean_dir(raw: str) -> str:
    path = (raw or "").strip().strip("/")
    if any(part in ("", ".", "..") for part in path.split("/")) and path:
        raise ValidationError("结果目录要写成房间里的相对路径，例如 周报/")
    return path


def describe_trigger(routine: Routine) -> str:
    trigger = RoutineTrigger(routine.trigger)
    if trigger is RoutineTrigger.schedule:
        return schedule.describe(routine.spec, routine.timezone)
    scope = "本房间" if routine.spec.get("scope") == "room" else "整个项目"
    return f"{TRIGGER_LABELS[trigger]}（{scope}）"


def _validate(trigger: str, spec: dict, tz: str) -> dict:
    try:
        kind = RoutineTrigger(trigger)
    except ValueError as exc:
        raise ValidationError("触发方式只能是定时或三种项目事件之一") from exc
    if kind is RoutineTrigger.schedule:
        return schedule.normalize(spec or {}, tz)
    scope = (spec or {}).get("scope", "room")
    if scope not in ("room", "project"):
        raise ValidationError("事件范围只能是 room 或 project")
    if kind is RoutineTrigger.library_file_added:
        scope = "project"
    return {"scope": scope}


class RoutineService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, routine_id: uuid.UUID) -> Routine:
        row = await self._session.get(Routine, routine_id)
        if row is None:
            raise NotFoundError("没有这条周期任务")
        return row

    async def list(
        self, project_id: uuid.UUID, *, topic_id: uuid.UUID | None = None
    ) -> list[Routine]:
        query = select(Routine).where(Routine.project_id == project_id)
        if topic_id is not None:
            query = query.where(Routine.topic_id == topic_id)
        return list(await self._session.scalars(query.order_by(Routine.created_at)))

    async def runs(self, routine_id: uuid.UUID, limit: int = 50) -> list[RoutineRun]:
        return list(
            await self._session.scalars(
                select(RoutineRun)
                .where(RoutineRun.routine_id == routine_id)
                .order_by(RoutineRun.created_at.desc())
                .limit(limit)
            )
        )

    async def _agent_for(self, topic_id: uuid.UUID, requested: str | None) -> str:
        seats = await TopicMemberService(self._session).agent_handles(topic_id)
        if requested:
            if requested not in seats:
                raise ValidationError("执行者必须是这个房间里的 AI 队友")
            return requested
        if not seats:
            raise ValidationError("这个房间里没有 AI 队友，周期任务没有人执行")
        return seats[0]

    async def create(
        self,
        *,
        topic: Topic,
        by: str,
        by_agent: bool,
        title: str,
        instructions: str,
        context_scope: str,
        output_dir: str,
        trigger: str,
        spec: dict,
        tz: str,
        owner_handle: str | None,
        agent_handle: str | None,
    ) -> Routine:
        if not title.strip() or not instructions.strip():
            raise ValidationError("周期任务要有名称和工作内容")
        spec = _validate(trigger, spec, tz)
        if by_agent:
            agent = await self._agent_for(topic.id, agent_handle or by)
            if not owner_handle:
                raise ValidationError("芝士起草时要写明是替谁设的（owner_handle）")
            owner = owner_handle
        else:
            agent = await self._agent_for(topic.id, agent_handle)
            owner = by
        row = Routine(
            id=uuid.uuid4(),
            project_id=topic.project_id,
            topic_id=topic.id,
            title=title.strip()[:200],
            instructions=instructions.strip(),
            context_scope=(context_scope or "").strip(),
            output_dir=_clean_dir(output_dir),
            trigger=trigger,
            spec=spec,
            timezone=tz,
            state=RoutineState.draft.value,
            agent_handle=agent,
            owner_handle=owner,
            proposed_by=by,
        )
        self._session.add(row)
        await self._session.flush()
        if by_agent:
            self._session.add(
                Block(
                    id=uuid.uuid4(),
                    project_id=row.project_id,
                    topic_id=row.topic_id,
                    author="system",
                    author_type=AuthorType.platform,
                    kind=BlockKind.event,
                    content=f"芝士起草了「{row.title}」，要你确认后才会执行",
                    meta={
                        **notice(
                            EVENT_ROUTINE_PROPOSED,
                            severity=SEVERITY_INFO,
                            who=WHO_CHEESE,
                            detail=self.summary_text(row),
                            detail_label="待确认的配置",
                        ),
                        "routine_id": str(row.id),
                    },
                )
            )
        else:
            self._activate(row, confirmed_by=by)
        await self._session.flush()
        return row

    @staticmethod
    def summary_text(row: Routine) -> str:
        lines = [
            f"名称：{row.title}",
            f"触发：{describe_trigger(row)}",
            f"工作内容：{row.instructions}",
            f"资料范围：{row.context_scope or '（未限定）'}",
            f"结果保存到：房间 {row.output_dir or '根目录'}",
            f"执行者：{row.agent_handle}",
            f"结果通知：{row.owner_handle}",
        ]
        return "\n".join(lines)

    def _activate(self, row: Routine, *, confirmed_by: str | None = None) -> None:
        stamp = now()
        row.state = RoutineState.active.value
        if confirmed_by is not None:
            row.confirmed_by = confirmed_by
            row.confirmed_at = stamp
        row.event_cursor = stamp
        row.next_run_at = (
            schedule.next_after(row.spec, row.timezone, stamp)
            if row.trigger == RoutineTrigger.schedule
            else None
        )

    async def confirm(self, row: Routine, *, by: str) -> Routine:
        if row.state != RoutineState.draft.value:
            raise ValidationError("这条已经确认过了")
        self._activate(row, confirmed_by=by)
        await self._session.flush()
        return row

    async def pause(self, row: Routine) -> Routine:
        if row.state != RoutineState.active.value:
            raise ValidationError("只有执行中的规则能暂停")
        row.state = RoutineState.paused.value
        row.next_run_at = None
        await self._session.flush()
        return row

    async def resume(self, row: Routine) -> Routine:
        """Continue from the next future moment; what was missed is not replayed."""
        if row.state != RoutineState.paused.value:
            raise ValidationError("只有暂停中的规则能恢复")
        self._activate(row)
        await self._session.flush()
        return row

    async def update(self, row: Routine, *, by_agent: bool, changes: dict) -> Routine:
        trigger = changes.get("trigger", row.trigger)
        spec = changes.get("spec", row.spec)
        tz = changes.get("timezone", row.timezone)
        new_spec = _validate(trigger, spec, tz)
        for key in ("title", "instructions", "context_scope"):
            if key in changes and changes[key] is not None:
                setattr(row, key, str(changes[key]).strip())
        if not row.title or not row.instructions:
            raise ValidationError("周期任务要有名称和工作内容")
        if changes.get("output_dir") is not None:
            row.output_dir = _clean_dir(changes["output_dir"])
        if changes.get("agent_handle"):
            row.agent_handle = await self._agent_for(
                row.topic_id, changes["agent_handle"]
            )
        row.trigger, row.spec, row.timezone = trigger, new_spec, tz
        row.revision += 1
        if by_agent and row.state != RoutineState.draft.value:
            # An AI teammate's edit is a new proposal: nothing it wrote runs
            # until a person has read and confirmed it.
            row.state = RoutineState.draft.value
            row.next_run_at = None
        elif row.state == RoutineState.active.value:
            self._activate(row)
        await self._session.flush()
        return row

    async def run_now(self, row: Routine, *, by: str) -> RoutineRun:
        if row.state == RoutineState.draft.value:
            raise ValidationError("还没确认的规则不能执行")
        run = await _fire(
            self._session,
            row,
            occurrence_key=f"manual:{uuid.uuid4()}",
            detail=f"由 {by} 手动触发。",
            scheduled_for=None,
        )
        assert run is not None
        await self._session.flush()
        return run

    async def delete(self, row: Routine) -> None:
        await self._session.delete(row)
        await self._session.flush()

    async def report(
        self,
        run: RoutineRun,
        *,
        by: str,
        status: str,
        summary: str,
        outputs: list[str],
    ) -> RoutineRun:
        routine = await self.get(run.routine_id)
        if by != routine.agent_handle:
            raise ForbiddenError("只有执行这条周期任务的 AI 队友能交回结果")
        if status not in (RunStatus.succeeded, RunStatus.failed):
            raise ValidationError("status 只能是 succeeded 或 failed")
        if run.status in TERMINAL_RUN_STATUSES:
            raise ValidationError("这次执行已经结束了")
        if status == RunStatus.failed and not summary.strip():
            raise ValidationError("失败要写明原因")
        run.status = status
        run.summary = summary.strip()
        run.outputs = [str(p) for p in outputs][:50]
        if status == RunStatus.failed:
            run.error = run.summary
        run.finished_at = now()
        run.started_at = run.started_at or run.finished_at
        await self._session.flush()
        return run


def run_prompt(routine: Routine, run: RoutineRun) -> str:
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
        f'platform_request(method="POST", path="/routine-runs/{run.id}/report", '
        'body={"status": "succeeded" 或 "failed", "summary": "一两句结果或失败原因",'
        ' "outputs": ["房间里的结果文件路径"]})',
        "没有交回结果的一次执行会被记为失败。",
    ]
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
    agent = await instance_for_seat(session, routine.project_id, routine.agent_handle)
    if agent is None or not agent.is_active:
        run.status = RunStatus.failed.value
        run.error = f"执行者 {routine.agent_handle} 已不在这个项目里"
        run.finished_at = stamp
        return run
    content = run_prompt(routine, run)
    event_id = uuid.uuid4()
    session.add(
        Block(
            id=event_id,
            project_id=routine.project_id,
            topic_id=routine.topic_id,
            author="system",
            author_type=AuthorType.platform,
            kind=BlockKind.event,
            content=f"开始执行「{routine.title}」",
            meta={
                **notice(
                    EVENT_ROUTINE_RUN,
                    severity=SEVERITY_INFO,
                    who=WHO_PLATFORM,
                    detail=content,
                    detail_label="交给芝士的工作",
                ),
                "routine_id": str(routine.id),
                "routine_run_id": str(run.id),
            },
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
        topic_id=routine.topic_id,
        instance_id=agent.id,
        content=content,
    )
    run.delivery_event_id = event_id
    return run


async def _fire_schedules(session: AsyncSession) -> int:
    stamp = now()
    rows = list(
        await session.scalars(
            select(Routine)
            .where(
                Routine.state == RoutineState.active.value,
                Routine.trigger == RoutineTrigger.schedule.value,
                Routine.next_run_at.is_not(None),
                Routine.next_run_at <= stamp,
            )
            .with_for_update(skip_locked=True)
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


async def _routine_turn_ids(session: AsyncSession, project_id: uuid.UUID) -> set:
    rows = await session.scalars(
        select(RoutineRun.turn_id)
        .join(Routine, Routine.id == RoutineRun.routine_id)
        .where(Routine.project_id == project_id, RoutineRun.turn_id.is_not(None))
    )
    return set(rows)


async def _events_for(
    session: AsyncSession, routine: Routine, since: datetime
) -> list[tuple[str, str, datetime]]:
    """(occurrence key, human description, when) for events after `since`."""
    trigger = RoutineTrigger(routine.trigger)
    in_room = routine.spec.get("scope") == "room"
    found: list[tuple[str, str, datetime]] = []
    if trigger is RoutineTrigger.library_file_added:
        for entry in library.list_library_files(routine.project_id):
            when = datetime.fromtimestamp(entry["modified"], UTC)
            if when > since:
                found.append(
                    (
                        f"library:{entry['path']}:{int(entry['modified'])}",
                        f"资料库新增了《{entry['path']}》。",
                        when,
                    )
                )
    elif trigger is RoutineTrigger.task_closed:
        query = select(Task).where(
            Task.project_id == routine.project_id,
            Task.closed_at.is_not(None),
            Task.closed_at > since,
        )
        if in_room:
            query = query.where(Task.room_id == routine.topic_id)
        own_turns = await _routine_turn_ids(session, routine.project_id)
        for task in await session.scalars(query):
            # Work a routine run started does not start routine work again.
            if (
                task.execution_turn_id is not None
                and task.execution_turn_id in own_turns
            ):
                continue
            assert task.closed_at is not None
            found.append(
                (
                    f"task:{task.id}",
                    f"任务《{task.title}》（{task.id}）已完成。",
                    task.closed_at,
                )
            )
    elif trigger is RoutineTrigger.card_accepted:
        query = select(AcceptCard).where(
            AcceptCard.status == AcceptStatus.accepted,
            AcceptCard.decided_at.is_not(None),
            AcceptCard.decided_at > since,
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
            .where(
                Routine.state == RoutineState.active.value,
                Routine.trigger.in_([t.value for t in EVENT_TRIGGERS]),
            )
            .with_for_update(skip_locked=True)
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
        if turn is not None and turn.stopped_at is not None:
            if turn.delivered_at is None:
                run.status = RunStatus.failed.value
                run.error = "这一轮没能开始：" + (
                    await _turn_failure(session, turn.id)
                    or delivery.last_error
                    or "执行环境没有接住这次工作"
                )
                run.finished_at = turn.stopped_at
                continue
            if run.status == RunStatus.queued.value:
                run.status = RunStatus.running.value
                run.started_at = turn.delivered_at
            if stamp - turn.stopped_at > REPORT_GRACE:
                run.status = RunStatus.failed.value
                run.error = "AI 队友这一轮已经结束，但没有交回结果"
                run.finished_at = turn.stopped_at
            continue
        if turn is not None and turn.delivered_at is not None:
            if run.status == RunStatus.queued.value:
                run.status = RunStatus.running.value
                run.started_at = turn.delivered_at
            continue
        if stamp - run.created_at > START_TIMEOUT:
            run.status = RunStatus.failed.value
            reason = delivery.last_error or "执行环境没有接单（设备离线或排队过久）"
            run.error = (
                f"{int(START_TIMEOUT.total_seconds() // 3600)} 小时内没有开始：{reason}"
            )
            run.finished_at = stamp


async def _announce_finished(session: AsyncSession) -> None:
    from app.domain.notification.services import ProjectNotificationService

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
    for run, routine in rows:
        run.notified = True
        topic = await session.get(Topic, routine.topic_id)
        if topic is None or topic.status == TopicStatus.archived:
            continue
        ok_ = run.status == RunStatus.succeeded.value
        verdict = {"succeeded": "已完成", "failed": "失败", "skipped": "未执行"}[
            run.status
        ]
        body = run.summary if ok_ else (run.error or run.summary)
        if run.outputs:
            body += "\n结果文件：" + "、".join(run.outputs)
        session.add(
            Block(
                id=uuid.uuid4(),
                project_id=routine.project_id,
                topic_id=routine.topic_id,
                author="system",
                author_type=AuthorType.platform,
                kind=BlockKind.event,
                content=f"「{routine.title}」{verdict}",
                meta={
                    **notice(
                        EVENT_ROUTINE_RESULT,
                        severity=SEVERITY_INFO if ok_ else SEVERITY_ERROR,
                        who=WHO_PLATFORM,
                        detail=body,
                        detail_label="结果" if ok_ else "原因",
                    ),
                    "routine_id": str(routine.id),
                    "routine_run_id": str(run.id),
                    "outputs": run.outputs,
                },
            )
        )
        await ProjectNotificationService(session).create(
            project_id=routine.project_id,
            level=NotificationLevel.light if ok_ else NotificationLevel.light,
            kind=NotificationType.CHANGE_ALERT,
            title=f"周期任务「{routine.title}」{verdict}",
            body=body,
            target_handle=routine.owner_handle,
            topic_id=routine.topic_id,
            payload={"routine_id": str(routine.id), "routine_run_id": str(run.id)},
        )


async def sweep(sessions: SessionFactory, *, chat, runner) -> dict[str, int]:
    async with sessions() as session:
        scheduled = await _fire_schedules(session)
        triggered = await _fire_events(session)
        await session.commit()
    async with sessions() as session:
        await _settle_open_runs(session)
        await _announce_finished(session)
        await session.commit()
    dispatched = 0
    if scheduled or triggered:
        dispatched = await dispatch_pending(sessions, chat=chat, runner=runner)
    return {"scheduled": scheduled, "triggered": triggered, "dispatched": dispatched}
