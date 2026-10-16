"""规则自己那一行：草稿、确认、启停、改配置、删，和一次手动执行。

一条规则的生命周期全在这里 —— 谁能建、谁能改、改了之后要不要重新等人确认、
下一次什么时候响（`_activate`）。「现在该不该响」是 `scheduling` 的事，
「响了之后」是 `firing` / `settlement` 的事。

`RoutineService` 上的同名方法是转发。`list` 是唯一改了名的：模块级的 `list`
会盖掉内建，而 `runs` 要用内建的 `list(...)`。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say, with_keys
from app.domain.agent.announce import settle_proposal
from app.domain.agent_instance.adapters import project_team
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.feedback import claims as feedback_claims
from app.domain.notice_meta import (
    EVENT_ROUTINE_PROPOSED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
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
from app.domain.routine.trigger import describe_trigger
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService

if TYPE_CHECKING:
    from app.domain.routine import service as pkg


def _clean_dir(raw: str) -> str:
    path = (raw or "").strip().strip("/")
    if any(part in ("", ".", "..") for part in path.split("/")) and path:
        raise ValidationError(say("routineOutputDirInvalid"))
    return path


def _validate(trigger: str, spec: dict, tz: str) -> dict:
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
    svc: pkg.RoutineService, routine_id: uuid.UUID, limit: int = 50
) -> list[RoutineRun]:
    return list(
        await svc._session.scalars(
            select(RoutineRun)
            .where(RoutineRun.routine_id == routine_id)
            .order_by(RoutineRun.created_at.desc())
            .limit(limit)
        )
    )


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
    context_scope: str,
    output_dir: str,
    trigger: str,
    spec: dict,
    tz: str,
    owner_handle: str | None,
    agent_handle: str | None,
) -> Routine:
    if not title.strip() or not instructions.strip():
        raise ValidationError(say("routineFieldsRequired"))
    spec = _validate(trigger, spec, tz)
    await svc._check_feedback_batch(spec, topic.project_id)
    if by_agent:
        agent = await svc._agent_for(topic.id, agent_handle or by)
        if not owner_handle:
            raise ValidationError(say("routineDraftNeedsOwner"))
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
    svc._session.add(row)
    await svc._session.flush()
    if by_agent:
        line = say("routineProposedBy", who=f"<@{by}>", title=row.title)
        svc._session.add(
            Block(
                id=uuid.uuid4(),
                project_id=row.project_id,
                conversation_id=row.topic_id,
                author="system",
                author_type=AuthorType.platform,
                kind=BlockKind.event,
                content=line,
                # A row, not `BlockRepository.add`: the key is recorded here.
                meta=with_keys(
                    {
                        **notice(
                            EVENT_ROUTINE_PROPOSED,
                            severity=SEVERITY_INFO,
                            # It waits on a person confirming it.
                            who=WHO_HUMAN,
                            detail=svc.summary_text(row),
                            detail_label=say("labelRoutineToConfirm"),
                        ),
                        "routine_id": str(row.id),
                    },
                    content=line,
                ),
            )
        )
    else:
        svc._activate(row, confirmed_by=by)
    await svc._session.flush()
    return row


def summary_text(row: Routine) -> str:
    return say(
        "routineSummary",
        title=row.title,
        trigger=describe_trigger(row),
        instructions=row.instructions,
        scope=row.context_scope or say("routineScopeUnset"),
        output=row.output_dir or say("routineOutputRoot"),
        agent=row.agent_handle,
        owner=row.owner_handle,
    )


def _activate(
    svc: pkg.RoutineService, row: Routine, *, confirmed_by: str | None = None
) -> None:
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


async def confirm(svc: pkg.RoutineService, row: Routine, *, by: str) -> Routine:
    if row.state != RoutineState.draft.value:
        raise ValidationError(say("routineAlreadyConfirmed"))
    svc._activate(row, confirmed_by=by)
    await svc._session.flush()
    await svc._settled(
        row,
        say(
            "routineProposalConfirmedBy",
            who=f"<@{row.proposed_by}>",
            title=row.title,
        ),
    )
    return row


async def _settled(svc: pkg.RoutineService, row: Routine, content: str) -> None:
    """A person decided on a teammate's draft: its line in the room says so."""
    await settle_proposal(
        svc._session,
        row.topic_id,
        event_type=EVENT_ROUTINE_PROPOSED,
        key="routine_id",
        value=row.id,
        content=content,
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


async def update(
    svc: pkg.RoutineService, row: Routine, *, by_agent: bool, changes: dict
) -> Routine:
    trigger = changes.get("trigger", row.trigger)
    spec = changes.get("spec", row.spec)
    tz = changes.get("timezone", row.timezone)
    new_spec = _validate(trigger, spec, tz)
    await svc._check_feedback_batch(new_spec, row.project_id)
    for key in ("title", "instructions", "context_scope"):
        if key in changes and changes[key] is not None:
            setattr(row, key, str(changes[key]).strip())
    if not row.title or not row.instructions:
        raise ValidationError(say("routineFieldsRequired"))
    if changes.get("output_dir") is not None:
        row.output_dir = _clean_dir(changes["output_dir"])
    if changes.get("agent_handle"):
        row.agent_handle = await svc._agent_for(row.topic_id, changes["agent_handle"])
    row.trigger, row.spec, row.timezone = trigger, new_spec, tz
    row.revision += 1
    if by_agent and row.state != RoutineState.draft.value:
        # An AI teammate's edit is a new proposal: nothing it wrote runs
        # until a person has read and confirmed it.
        row.state = RoutineState.draft.value
        row.next_run_at = None
    elif row.state == RoutineState.active.value:
        svc._activate(row)
    await svc._session.flush()
    return row


async def run_now(svc: pkg.RoutineService, row: Routine, *, by: str) -> RoutineRun:
    if row.state == RoutineState.draft.value:
        raise ValidationError(say("routineRunUnconfirmed"))
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


async def delete(svc: pkg.RoutineService, row: Routine) -> None:
    if row.state == RoutineState.draft.value:
        await svc._settled(
            row,
            say(
                "routineProposalDeletedBy",
                who=f"<@{row.proposed_by}>",
                title=row.title,
            ),
        )
    await svc._session.delete(row)
    await svc._session.flush()
