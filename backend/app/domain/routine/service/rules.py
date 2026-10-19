"""规则自己那一行：建、启停、改配置、删，和一次手动执行。

一条规则的生命周期全在这里 —— 谁能建、谁能改、改了什么时候生效、下一次什么时候
响（`_activate`）。规则没有人确认这一步：人建的存下来就在跑，芝士建的也一样，建完
由芝士自己在对话里说一句，平台不另插一行。「现在该不该响」是 `scheduling` 的事，
「响了之后」是 `firing` / `settlement` 的事。

`RoutineService` 上的同名方法是转发。`list` 是唯一改了名的：模块级的 `list`
会盖掉内建，而 `runs` 要用内建的 `list(...)`。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.announce import settle_proposal
from app.domain.agent_instance.adapters import project_team
from app.domain.feedback import claims as feedback_claims
from app.domain.notice_meta import EVENT_ROUTINE_PROPOSED
from app.domain.routine import schedule
from app.domain.routine.models import (
    Routine,
    RoutineRun,
    RoutineState,
    RoutineTrigger,
)
from app.domain.routine.service._shared import (
    now,
)
from app.domain.routine.service.firing import _fire
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService

if TYPE_CHECKING:
    from app.domain.routine import service as pkg


def name_of(title: str, instructions: str) -> str:
    """A name for what the goal says —— a rule's, or a saved template's: what
    was typed, or the goal's first sentence.

    Only the goal is required, so the name usually comes from the first thing
    that goal says —— 「每周一汇总各频道进展。写成三五条」 叫「每周一汇总各频道进展」。
    """
    name = (title or "").strip()
    goal = (instructions or "").strip()
    if not name:
        cut = len(goal)
        for stop in ("。", "！", "？", "\n", ". ", "! ", "? "):
            at = goal.find(stop)
            if at > 0:
                cut = min(cut, at)
        name = goal[:cut].strip()
    return name[:200]


def validate_spec(trigger: str, spec: dict, tz: str) -> dict:
    try:
        kind = RoutineTrigger(trigger)
    except ValueError as exc:
        raise ValidationError(say("routineTriggerInvalid")) from exc
    if kind is RoutineTrigger.schedule:
        return schedule.normalize(spec or {}, tz)
    scope = (spec or {}).get("scope", "room")
    if scope not in ("room", "project"):
        raise ValidationError(say("routineEventScopeInvalid"))
    if kind is RoutineTrigger.library_file_added:
        scope = "project"
    return {"scope": scope}


async def _check_feedback_batch(
    svc: pkg.RoutineService, spec: dict, project_id: uuid.UUID
) -> None:
    """Feedback triage runs only where the teammate may claim what it is handed.

    Claiming is open to rooms of the projects working on the platform itself
    (`feedback.claims`); a room elsewhere would be handed reports it can read
    but not take, every day.
    """
    if spec.get("feedback_batch") and not await feedback_claims.is_platform_project(
        svc._session, project_id
    ):
        raise ValidationError(say("routineFeedbackBatchPlatformOnly"))


async def get(svc: pkg.RoutineService, routine_id: uuid.UUID) -> Routine:
    row = await svc._session.get(Routine, routine_id)
    if row is None:
        raise NotFoundError(say("routineNotFound"))
    return row


async def list_for_project(
    svc: pkg.RoutineService, project_id: uuid.UUID, *, topic_id: uuid.UUID | None = None
) -> list[Routine]:
    query = select(Routine).where(Routine.project_id == project_id)
    if topic_id is not None:
        query = query.where(Routine.topic_id == topic_id)
    return list(await svc._session.scalars(query.order_by(Routine.created_at)))


# 上面这个函数在类上仍叫 `list`（见模块 docstring 里改名那条）。


async def runs(
    svc: pkg.RoutineService,
    routine_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int = 50,
    failed_only: bool = False,
) -> tuple[list[RoutineRun], int]:
    """这条规则的一页执行记录，外加符合条件的**总数**。

    倒序给，最近的一次在最前面。「没能执行」是失败和未执行两种 —— 那一颗筛子问的
    是「哪几次没成」，不是状态本身叫什么，所以两种一起来。

    总数和这一页一起回：翻页器上那句「共 N 次」说的是筛过之后的全部，不是这一页
    有几行，分两次问就会在两次之间变。
    """
    where = [RoutineRun.routine_id == routine_id]
    if failed_only:
        where.append(RoutineRun.status.in_(("failed", "skipped")))
    total = await svc._session.scalar(
        select(func.count()).select_from(RoutineRun).where(*where)
    )
    rows = await svc._session.scalars(
        select(RoutineRun)
        .where(*where)
        .order_by(RoutineRun.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(rows), int(total or 0)


async def latest_run_statuses(
    svc: pkg.RoutineService, routine_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """这些规则各自最近一次执行跑成什么样，一次查询问完。

    行上那颗图形说的不止「启用中 / 已暂停」：正在排队的那条画沙漏，上一次没跑成的
    那条画红叉。规则自己那一行不记最近一次跑得怎么样，所以列表上这两档只能问执行
    记录 —— 一条一条问就是每行一次往返。
    """
    if not routine_ids:
        return {}
    stmt = (
        select(RoutineRun.routine_id, RoutineRun.status)
        .where(RoutineRun.routine_id.in_(routine_ids))
        .order_by(RoutineRun.routine_id, RoutineRun.created_at.desc())
    )
    latest: dict[uuid.UUID, str] = {}
    for routine_id, status in (await svc._session.execute(stmt)).all():
        latest.setdefault(routine_id, status)
    return latest


async def _agent_for(
    svc: pkg.RoutineService, topic_id: uuid.UUID, requested: str | None
) -> str:
    seats = await TopicMemberService(svc._session).agent_handles(topic_id)
    if requested:
        if requested not in seats:
            raise ValidationError(say("routineAgentNotInRoom"))
        return requested
    if not seats:
        raise ValidationError(say("routineNoAgentInRoom"))
    return seats[0]


async def create(
    svc: pkg.RoutineService,
    *,
    topic: Topic,
    by: str,
    by_agent: bool,
    title: str,
    instructions: str,
    note: str,
    trigger: str,
    spec: dict,
    tz: str,
    owner_handle: str | None,
    agent_handle: str | None,
) -> Routine:
    if not instructions.strip():
        raise ValidationError(say("routineInstructionsRequired"))
    spec = validate_spec(trigger, spec, tz)
    await svc._check_feedback_batch(spec, topic.project_id)
    if by_agent:
        agent = await svc._agent_for(topic.id, agent_handle or by)
        if not owner_handle:
            raise ValidationError(say("routineNeedsOwner"))
        # The owner is told the results, so they are a person of the
        # project, and the channel the routine runs in becomes theirs too.
        members = TopicMemberService(svc._session)
        if owner_handle not in await members.project_people(
            topic.project_id, team=project_team
        ):
            raise ValidationError(say("routineOwnerNotInProject", handle=owner_handle))
        await members.take_in(topic.id, owner_handle)
        owner = owner_handle
    else:
        agent = await svc._agent_for(topic.id, agent_handle)
        owner = by
    row = Routine(
        id=uuid.uuid4(),
        project_id=topic.project_id,
        topic_id=topic.id,
        title=name_of(title, instructions),
        instructions=instructions.strip(),
        note=(note or "").strip(),
        trigger=trigger,
        spec=spec,
        timezone=tz,
        state=RoutineState.active.value,
        agent_handle=agent,
        owner_handle=owner,
        created_by=by,
    )
    svc._session.add(row)
    svc._activate(row)
    await svc._session.flush()
    return row


def _activate(svc: pkg.RoutineService, row: Routine) -> None:
    stamp = now()
    row.state = RoutineState.active.value
    row.event_cursor = stamp
    row.next_run_at = (
        schedule.next_after(row.spec, row.timezone, stamp)
        if row.trigger == RoutineTrigger.schedule
        else None
    )


async def pause(svc: pkg.RoutineService, row: Routine) -> Routine:
    if row.state != RoutineState.active.value:
        raise ValidationError(say("routinePauseActiveOnly"))
    row.state = RoutineState.paused.value
    row.next_run_at = None
    await svc._session.flush()
    return row


async def resume(svc: pkg.RoutineService, row: Routine) -> Routine:
    """Continue from the next future moment; what was missed is not replayed."""
    if row.state != RoutineState.paused.value:
        raise ValidationError(say("routineResumePausedOnly"))
    svc._activate(row)
    await svc._session.flush()
    return row


async def update(svc: pkg.RoutineService, row: Routine, *, changes: dict) -> Routine:
    trigger = changes.get("trigger", row.trigger)
    spec = changes.get("spec", row.spec)
    tz = changes.get("timezone", row.timezone)
    new_spec = validate_spec(trigger, spec, tz)
    await svc._check_feedback_batch(new_spec, row.project_id)
    for key in ("title", "instructions", "note"):
        if key in changes and changes[key] is not None:
            setattr(row, key, str(changes[key]).strip())
    if not row.instructions:
        raise ValidationError(say("routineInstructionsRequired"))
    if not row.title:
        row.title = name_of(row.title, row.instructions)
    if changes.get("agent_handle"):
        row.agent_handle = await svc._agent_for(row.topic_id, changes["agent_handle"])
    row.trigger, row.spec, row.timezone = trigger, new_spec, tz
    row.revision += 1
    # An edit takes effect the same way a new rule does: an AI teammate's
    # edit needs nobody's confirmation either.
    if row.state == RoutineState.active.value:
        svc._activate(row)
    await svc._session.flush()
    return row


async def run_now(svc: pkg.RoutineService, row: Routine, *, by: str) -> RoutineRun:
    run = await _fire(
        svc._session,
        row,
        occurrence_key=f"manual:{uuid.uuid4()}",
        detail=f"由 {by} 手动触发。",
        scheduled_for=None,
    )
    assert run is not None
    await svc._session.flush()
    return run


async def delete(svc: pkg.RoutineService, row: Routine, *, by: str) -> None:
    # 早先的版本建完会落一行「多了这么一条」；规则删了，它还那样写就会把人指到
    # 一条不存在的规则上，所以原地改成删掉的样子。新版不再落这样的行，这只收拾
    # 旧数据。
    await svc._settled(
        row,
        say("routineProposalDeletedBy", who=f"<@{by}>", title=row.title),
    )
    await svc._session.delete(row)
    await svc._session.flush()


async def _settled(svc: pkg.RoutineService, row: Routine, content: str) -> None:
    """一条提到这条规则的行，改成它的下落。"""
    await settle_proposal(
        svc._session,
        row.topic_id,
        event_type=EVENT_ROUTINE_PROPOSED,
        key="routine_id",
        value=row.id,
        content=content,
    )
