"""Routines: create and govern the rules, fire them, and settle each run.

A run is one message of the teammate's in the channel's main line, and the run
itself happens in that message's 支线, where a follow-up question reaches the
same session. Firing writes the run row, the message, the 支线's opening line
and the agent's delivery in one transaction; the delivery ledger then owns
getting the prompt to the teammate. A run is settled by the teammate's own
report, or, when the turn ends without one or never starts, by what the ledger
and the turn record show. When it is settled the message says how it went and
moves to the bottom of the main line, at the moment it finished.

This module keeps rule lifecycle CRUD (`RoutineService`), main-line run-message
publishing (`publish_run_messages`), and the periodic `sweep` entry point.
Schedule/event firing lives in `firing.py`, and open-run settlement, owner
notifications, and archived-room reconciliation live in `settlement.py`; every
name originally defined here is re-exported below.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say, with_keys
from app.domain.agent.announce import settle_proposal
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent_instance.adapters import project_team
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.delivery.agent import dispatch_pending
from app.domain.feedback import claims as feedback_claims
from app.domain.notice_meta import (
    EVENT_ROUTINE_PROPOSED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.routine import schedule
from app.domain.routine.firing import (
    EVENT_RUNS_PER_HOUR,
    EVENT_TRIGGERS,
    MISSED_GRACE,
    NO_FEEDBACK_WAITING,
    TRIGGER_LABELS,
    _events_for,
    _fire,
    _fire_events,
    _fire_schedules,
    _previous_result,
    now,
    run_prompt,
)
from app.domain.routine.models import (
    TERMINAL_RUN_STATUSES,
    Routine,
    RoutineRun,
    RoutineState,
    RoutineTrigger,
    RunStatus,
)
from app.domain.routine.reads import runs_under
from app.domain.routine.settlement import (
    NO_REPORT,
    REPORT_TIMEOUT,
    START_TIMEOUT,
    _announce_finished,
    _settle_open_runs,
    _turn_failure,
    announce_archived_rooms,
    archived_room_ids,
)
from app.domain.routine.trigger import describe_trigger
from app.domain.thread import reads as thread_reads
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService


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


class RoutineService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _check_feedback_batch(self, spec: dict, project_id: uuid.UUID) -> None:
        """Feedback triage runs only where the teammate may claim what it is handed.

        Claiming is open to rooms of the projects working on the platform itself
        (`feedback.claims`); a room elsewhere would be handed reports it can read
        but not take, every day.
        """
        if spec.get("feedback_batch") and not await feedback_claims.is_platform_project(
            self._session, project_id
        ):
            raise ValidationError(say("routineFeedbackBatchPlatformOnly"))

    async def get(self, routine_id: uuid.UUID) -> Routine:
        row = await self._session.get(Routine, routine_id)
        if row is None:
            raise NotFoundError(say("routineNotFound"))
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
                raise ValidationError(say("routineAgentNotInRoom"))
            return requested
        if not seats:
            raise ValidationError(say("routineNoAgentInRoom"))
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
            raise ValidationError(say("routineFieldsRequired"))
        spec = _validate(trigger, spec, tz)
        await self._check_feedback_batch(spec, topic.project_id)
        if by_agent:
            agent = await self._agent_for(topic.id, agent_handle or by)
            if not owner_handle:
                raise ValidationError(say("routineDraftNeedsOwner"))
            # The owner is told the results, so they are a person of the
            # project, and the channel the routine runs in becomes theirs too.
            members = TopicMemberService(self._session)
            if owner_handle not in await members.project_people(
                topic.project_id, team=project_team
            ):
                raise ValidationError(
                    say("routineOwnerNotInProject", handle=owner_handle)
                )
            await members.take_in(topic.id, owner_handle)
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
            line = say("routineProposedBy", who=f"<@{by}>", title=row.title)
            self._session.add(
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
                                detail=self.summary_text(row),
                                detail_label=say("labelRoutineToConfirm"),
                            ),
                            "routine_id": str(row.id),
                        },
                        content=line,
                    ),
                )
            )
        else:
            self._activate(row, confirmed_by=by)
        await self._session.flush()
        return row

    @staticmethod
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
            raise ValidationError(say("routineAlreadyConfirmed"))
        self._activate(row, confirmed_by=by)
        await self._session.flush()
        await self._settled(
            row,
            say(
                "routineProposalConfirmedBy",
                who=f"<@{row.proposed_by}>",
                title=row.title,
            ),
        )
        return row

    async def _settled(self, row: Routine, content: str) -> None:
        """A person decided on a teammate's draft: its line in the room says so."""
        await settle_proposal(
            self._session,
            row.topic_id,
            event_type=EVENT_ROUTINE_PROPOSED,
            key="routine_id",
            value=row.id,
            content=content,
        )

    async def pause(self, row: Routine) -> Routine:
        if row.state != RoutineState.active.value:
            raise ValidationError(say("routinePauseActiveOnly"))
        row.state = RoutineState.paused.value
        row.next_run_at = None
        await self._session.flush()
        return row

    async def resume(self, row: Routine) -> Routine:
        """Continue from the next future moment; what was missed is not replayed."""
        if row.state != RoutineState.paused.value:
            raise ValidationError(say("routineResumePausedOnly"))
        self._activate(row)
        await self._session.flush()
        return row

    async def update(self, row: Routine, *, by_agent: bool, changes: dict) -> Routine:
        trigger = changes.get("trigger", row.trigger)
        spec = changes.get("spec", row.spec)
        tz = changes.get("timezone", row.timezone)
        new_spec = _validate(trigger, spec, tz)
        await self._check_feedback_batch(new_spec, row.project_id)
        for key in ("title", "instructions", "context_scope"):
            if key in changes and changes[key] is not None:
                setattr(row, key, str(changes[key]).strip())
        if not row.title or not row.instructions:
            raise ValidationError(say("routineFieldsRequired"))
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
            raise ValidationError(say("routineRunUnconfirmed"))
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
        if row.state == RoutineState.draft.value:
            await self._settled(
                row,
                say(
                    "routineProposalDeletedBy",
                    who=f"<@{row.proposed_by}>",
                    title=row.title,
                ),
            )
        await self._session.delete(row)
        await self._session.flush()

    async def announce_archived_rooms(self) -> int:
        return await announce_archived_rooms(self._session)

    async def archived_room_ids(self, topic_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return await archived_room_ids(self._session, topic_ids)

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
        await self._session.flush()
        return run


async def publish_run_messages(
    sessions: SessionFactory, message_ids: list[uuid.UUID], *, new: bool
) -> None:
    """Runs' messages, whole, to whoever has their channel open: a new one as
    a message of the teammate's, a settled one as the line it replaces. Each
    carries what it is drawn with: how the run went, its reactions and the
    line under it of its 支线."""
    if not message_ids:
        return

    broker = get_broker()
    async with sessions() as session:
        messages = list(
            await session.scalars(select(Block).where(Block.id.in_(message_ids)))
        )
        runs = await runs_under(session, message_ids)
        threads = await thread_reads.under_messages(
            session,
            message_ids,
            replying=lambda thread: sorted(
                set(broker.activity.turn_agents(str(thread)).values())
            ),
        )
        reactions = await BlockRepository(session).reactions_for_blocks(message_ids)
    for message in messages:
        payload = BlockOut.model_validate(message).model_dump(mode="json")
        payload["routine_run"] = runs.get(message.id)
        if message.id in reactions:
            payload["reactions"] = reactions[message.id]
        if message.id in threads:
            payload["thread"] = threads[message.id]
        await broker.publish(
            str(message.conversation_id),
            {
                "type": "assistant_block" if new else "block_updated",
                "block": payload,
            },
        )


async def sweep(sessions: SessionFactory, *, chat, runner) -> dict[str, int]:
    began = now()
    async with sessions() as session:
        scheduled = await _fire_schedules(session)
        triggered = await _fire_events(session)
        await session.commit()
        started = [
            message
            for message in await session.scalars(
                select(RoutineRun.message_id).where(
                    RoutineRun.created_at >= began,
                    RoutineRun.message_id.is_not(None),
                )
            )
            if message is not None
        ]
    await publish_run_messages(sessions, started, new=True)
    async with sessions() as session:
        # 归档是别处做的动作，所以这里每次都问一遍「哪个房间的话还没说」，而不是
        # 让归档那几条路各自记得来敲这扇门（`announce_archived_rooms`）。
        stopped = await RoutineService(session).announce_archived_rooms()
        await _settle_open_runs(session)
        changed = await _announce_finished(session)
        await session.commit()
    await publish_run_messages(sessions, [m.id for m in changed], new=False)
    dispatched = 0
    if scheduled or triggered:
        dispatched = await dispatch_pending(sessions, chat=chat, runner=runner)
    return {
        "scheduled": scheduled,
        "triggered": triggered,
        "stopped": stopped,
        "dispatched": dispatched,
    }


__all__ = [
    "EVENT_RUNS_PER_HOUR",
    "EVENT_TRIGGERS",
    "MISSED_GRACE",
    "NO_FEEDBACK_WAITING",
    "NO_REPORT",
    "REPORT_TIMEOUT",
    "RoutineService",
    "START_TIMEOUT",
    "TRIGGER_LABELS",
    "_announce_finished",
    "_clean_dir",
    "_events_for",
    "_fire",
    "_fire_events",
    "_fire_schedules",
    "_previous_result",
    "_settle_open_runs",
    "_turn_failure",
    "_validate",
    "announce_archived_rooms",
    "archived_room_ids",
    "describe_trigger",
    "dispatch_pending",
    "now",
    "publish_run_messages",
    "run_prompt",
    "runs_under",
    "sweep",
]
