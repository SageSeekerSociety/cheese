"""Routine run settlement, owner notifications, and archived-room reconciliation."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sentences import listing, say
from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.adapters import project_team
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.models import Delivery
from app.domain.notice_meta import (
    EVENT_ROUTINE_STOPPED,
    EVENT_TURN_FAILED,
    EVENT_TURN_TIMEOUT,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.routine.firing import now
from app.domain.routine.models import (
    Routine,
    RoutineRun,
    RoutineState,
    RunStatus,
)
from app.domain.topic.models import Topic, TopicStatus

#: A queued run whose turn never began is reported as not started after this.
START_TIMEOUT = timedelta(hours=2)
#: How long a delivered run may go without a report before it counts as failed.
#: A turn row cannot say when the work ended: in a live session every input's
#: row is delivered and stopped within milliseconds, and the business backend
#: does not see whether the device's session is still busy. So the only clock
#: is the longest a turn may run at all, plus a little for the report to land;
#: a real failure is caught sooner by the room's own turn-failed notice.
REPORT_TIMEOUT = timedelta(seconds=settings.agent_turn_hard_ceiling_s) + timedelta(
    minutes=5
)
NO_REPORT = "AI 队友一直没有交回结果"


async def announce_archived_rooms(session: AsyncSession) -> int:
    """归档是别处做的动作：每次对账扫一遍，哪个房间的话还没说就补上。

    认房间的状态，不认是谁归档的 —— 手工归档、整个项目一起归档、脚本归档
    走的是同一个答案，话题那一域也就不用认识周期任务。

    规则自己那一行**不动**（结论：取消归档后从下一个时刻继续，
    ``_fire_schedules`` 一直是按房间状态挑的）。要说的是每个归档了、又还有
    在跑的规则的房间：房间里落一行「N 条规则已随归档停止」，加上给每条规则
    主人的一条通知。

    Idempotent per archive episode：房间自己的 ``archived_at`` 印在那行上，
    已经带着本轮印子的房间跳过 —— 所以取消归档后再归档会再说一次，而手工
    归档过的房间被项目归档又捎带一遍时不会再被说一次。

    Returns how many rules were announced as stopped.
    """
    rooms = list(
        await session.scalars(
            select(Topic)
            .where(Topic.status == TopicStatus.archived)
            .where(
                Topic.id.in_(
                    select(Routine.topic_id).where(
                        Routine.state == RoutineState.active.value
                    )
                )
            )
            # 和这一串里别的几步一样：两个后端进程同时扫，锁住的那间这次跳过，
            # 下一轮（30 秒后）再补 —— 不然同一段归档会被说两遍。
            .with_for_update(skip_locked=True)
        )
    )

    stopped = 0
    for room in rooms:
        rules = list(
            await session.scalars(
                select(Routine).where(
                    Routine.topic_id == room.id,
                    Routine.state == RoutineState.active.value,
                )
            )
        )
        if not rules:
            continue
        stamp = (room.archived_at or now()).isoformat()
        already = await session.scalar(
            select(Block.id)
            .where(
                Block.conversation_id == room.id,
                Block.meta["event_type"].as_string() == EVENT_ROUTINE_STOPPED,
                Block.meta["archived_at"].as_string() == stamp,
            )
            .limit(1)
        )
        if already is not None:
            continue
        session.add(
            Block(
                id=uuid.uuid4(),
                project_id=room.project_id,
                conversation_id=room.id,
                author="system",
                author_type=AuthorType.platform,
                kind=BlockKind.event,
                content=f"{len(rules)} 条规则已随归档停止",
                meta={
                    **notice(
                        EVENT_ROUTINE_STOPPED,
                        severity=SEVERITY_INFO,
                        who=WHO_PLATFORM,
                        detail="\n".join(f"「{r.title}」" for r in rules),
                        detail_label="规则",
                    ),
                    "routine_ids": [str(r.id) for r in rules],
                    "archived_at": stamp,
                },
            )
        )
        for rule in rules:
            await ProjectNotificationService(session, team=project_team).create(
                project_id=rule.project_id,
                level=NotificationLevel.light,
                kind=NotificationType.CHANGE_ALERT,
                title=f"周期任务「{rule.title}」已随话题归档停止",
                body=(
                    f"房间「{room.title}」归档了，这条规则随之停下。"
                    "取消归档后它会从下一个时刻继续，不用重新设置。"
                ),
                target_handle=rule.owner_handle,
                conversation_id=room.id,
                payload={"routine_id": str(rule.id)},
            )
        stopped += len(rules)
    await session.flush()
    return stopped


async def archived_room_ids(
    session: AsyncSession, topic_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """这些房间里哪些归档了，一次查询问完。

    规则的自己那一行不记「房间没了」（归档不写规则），所以列表上的
    「已随话题归档停止」只能问房间 —— 一条一条问就是每行一次往返。
    """
    if not topic_ids:
        return set()
    stmt = select(Topic.id).where(
        Topic.id.in_(topic_ids), Topic.status == TopicStatus.archived
    )
    return set((await session.scalars(stmt)).all())


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
