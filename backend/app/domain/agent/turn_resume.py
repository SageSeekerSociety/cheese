"""A seat whose turn failed on a transient model-service error goes on by itself.

Before this, such a seat stayed idle until a person or another agent poked it
(`resumable_failures` has the measurement). Now the failure is classified as it
lands (`after_failure`, called from the hook intake before any failure
card is posted), and a resumable one leaves a `TurnResume` row due after a
backoff, or at the reset the service named. A clock (`hand_due`) turns a due
row into one instruction on the delivery ledger (`delivery.agent.record_agent`),
so admission, leases, the closed-task check and the 30-second dispatch all stay
the ledger's, exactly as for a timed delivery.

What the session hears is `RESUME_NOTE`. It reaches the same session, which
still holds the conversation the failed turn was having, so "go on from where
it stopped" is all it needs to be told.

Why it cannot storm:

- **One owed resume per seat and conversation.** A partial unique index on the
  table. A second failure while one waits (something woke the seat in the
  meantime) moves that one row onto itself rather than adding another.
- **A streak has a cap.** Each resume in a row waits longer and the streak stops
  after `Policy.attempts`; the row that would have exceeded it is written as
  dropped, so the record says why the seat was left alone. A resumed turn that
  failed more than `STREAK_WINDOW` after it was handed over made progress, and
  starts a new streak; so does a person speaking. A platform interruption
  (`Policy.platform`, the upgrade replacing the runner a turn was in) is
  exempt: it is resumed after `base_s` whatever the streak stands at, and it
  does not count as one more attempt, because the agent did not fail.
- **It stops when it no longer applies.** At its due time a row is dropped, not
  handed over, when a person has spoken in the conversation since the failure,
  when the seat has run another turn since (somebody already woke it), or when
  the task closed, the room was archived or the seat left.
"""

from __future__ import annotations

import logging
import math
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import and_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.obs import scrub_secrets
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.gateway_usage import OWN_ROUTE
from app.domain.agent.models import AgentTurn, TurnResume
from app.domain.agent.own_limit import refused_until
from app.domain.agent.resumable_failures import (
    SUBSCRIPTION_COOLING,
    Resumable,
    classify,
)
from app.domain.agent.run_records import publish
from app.domain.agent.service import AgentResult
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.services import room_of, room_of_thread, rooms_of
from app.domain.delivery.agent import instance_for_seat, record_agent
from app.domain.delivery.models import Delivery
from app.domain.delivery_events import DeliveryEvent
from app.domain.event_author import AuthorType
from app.domain.identity.handles import agent_handle_column
from app.domain.notice_meta import (
    EVENT_TURN_FAILED,
    EVENT_TURN_RESUME_STARTED,
    EVENT_TURN_RESUME_STOPPED,
    EVENT_TURN_RESUME_WAIT,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
from app.domain.notification.models import NotificationType
from app.domain.room_task.models import Task, TaskStatus
from app.domain.run_record.models import RunRecord
from app.domain.run_record.service import as_payload, record
from app.domain.topic.models import Topic, TopicStatus
from app.domain.usage.claude_pool import read_claude_pool, snapshot_path

logger = logging.getLogger(__name__)

#: What the session is handed when the resume falls due. The agent reads it,
#: not a person, as with every platform instruction.
RESUME_NOTE: Final = say("turnResumeAfterFailure")

#: A resumed turn that failed again within this long of being handed over did
#: not get anywhere, and its next resume counts on from it. Past it, the turn
#: worked for a while: a fresh failure starts a fresh streak.
STREAK_WINDOW: Final = timedelta(hours=1)

#: Past a reset the service named, so the first request is not refused again by
#: a window that reopens a moment late.
_AFTER_RESET_S: Final = 30.0

#: Why a row was not handed over (`TurnResume.dropped_reason`).
ATTEMPTS_USED_UP: Final = "attempts_used_up"
NO_RESET: Final = "no_reset"
PERSON_SPOKE: Final = "person_spoke"
SEAT_RAN_AGAIN: Final = "seat_ran_again"
CLOSED: Final = "conversation_closed"
SEAT_GONE: Final = "seat_gone"
DELIVERY_FAILED: Final = "delivery_failed"

_SAID_MAX: Final = 4000


def _now() -> datetime:
    return datetime.now(UTC)


#: The predicate of the two partial indexes, spelled as the migration spells it
#: so `ON CONFLICT` names the unique one.
_OWED_SQL: Final = "handed_at IS NULL AND dropped_at IS NULL"


def _owed():
    return and_(TurnResume.handed_at.is_(None), TurnResume.dropped_at.is_(None))


async def after_failure(
    sessions: SessionFactory,
    *,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    seat: str | None,
    failed_turn_id: uuid.UUID,
    result: AgentResult,
    route: str | None,
    now: datetime | None = None,
) -> TurnResume | None:
    """Schedule this seat's resume when its failure is one that passes by
    itself; the row written, or None when nothing was scheduled."""
    now = now or _now()
    reset = refused_until(result.rate_limit, now)
    if route == OWN_ROUTE and reset is not None:
        # A member's own Claude Code at its account's window: `own_limit`
        # already handed the agent a note for the reset and told the room.
        return None
    found = classify(
        result.text,
        failure_code=result.failure_code,
        api_error_status=result.api_error_status,
        reset_wait_s=(reset - now).total_seconds() if reset is not None else None,
    )
    if found is None or not seat:
        return None
    recorded: tuple[dict, uuid.UUID | None] | None = None
    async with sessions() as session:
        instance = await instance_for_seat(session, project_id, seat)
        if instance is None or not instance.is_active:
            return None
        if not await _open(session, conversation_id):
            return None
        # Locked: a `hand_due` pass may be handing this row over or dropping
        # it right now. Waiting on its lock re-reads the row, and a row it
        # settled no longer matches, so this failure writes a row of its own
        # instead of moving one that is no longer owed.
        owed = await session.scalar(
            select(TurnResume)
            .where(
                TurnResume.conversation_id == conversation_id,
                TurnResume.recipient_handle == seat,
                _owed(),
            )
            .with_for_update()
        )
        if owed is not None:
            # Something woke the seat before its resume was due (a person, a
            # teammate, a delivery), and that turn failed too. The row now
            # speaks for this failure: left measuring from the earlier one,
            # the wake-up itself would drop it when due (`_no_longer_applies`)
            # and the latest failure would get no resume at all. A person's
            # word in between starts the streak again, as it does below.
            spoke = await _person_spoke(session, conversation_id, owed.failed_at)
            attempt = (0 if found.policy.platform else 1) if spoke else owed.attempt
            wait, dropped = _wait(found, attempt, now)
            owed.failed_turn_id = failed_turn_id
            owed.reason = found.kind
            owed.said = str(scrub_secrets(result.text or ""))[:_SAID_MAX]
            owed.attempt = attempt
            owed.failed_at = now
            owed.due_at = now + timedelta(seconds=wait or 0)
            if dropped:
                owed.dropped_at = now
                owed.dropped_reason = dropped
            recorded = await _record_schedule(session, owed, now)
            if recorded is None:
                return None
            await session.commit()
            logger.info(
                "turn resume: %s in %s failed again before its resume (%s); "
                "now due %s, attempt %s",
                seat,
                conversation_id,
                found.kind,
                owed.due_at.isoformat(),
                attempt,
            )
            stored = owed
        else:
            # A platform interruption carries the streak as it stands: it is
            # not one more failure of the agent's (`Policy.platform`).
            attempt = await _streak(session, conversation_id, seat, now) + (
                0 if found.policy.platform else 1
            )
            wait, dropped = _wait(found, attempt, now)
            row = {
                "id": uuid.uuid4(),
                "project_id": project_id,
                "conversation_id": conversation_id,
                "recipient_handle": seat,
                "agent_instance_id": instance.id,
                "failed_turn_id": failed_turn_id,
                "reason": found.kind,
                "said": str(scrub_secrets(result.text or ""))[:_SAID_MAX],
                "attempt": attempt,
                "failed_at": now,
                "due_at": now + timedelta(seconds=wait or 0),
                "dropped_at": now if dropped else None,
                "dropped_reason": dropped,
            }
            written = await session.scalar(
                insert(TurnResume)
                .values(**row)
                .on_conflict_do_nothing(
                    index_elements=["conversation_id", "recipient_handle"],
                    index_where=text(_OWED_SQL),
                )
                .returning(TurnResume.id)
            )
            if written is None:
                # Another failure of the same seat got there first.
                return None
            stored = await session.get(TurnResume, written)
            assert stored is not None
            recorded = await _record_schedule(session, stored, now)
            if recorded is None:
                return None
            await session.commit()
    assert recorded is not None
    payload, room = recorded
    await publish(payload, room=room)
    if dropped:
        logger.warning(
            "turn resume: %s in %s failed again (%s, attempt %s); not resuming: %s",
            seat,
            conversation_id,
            found.kind,
            attempt,
            dropped,
        )
        return stored
    logger.info(
        "turn resume: %s in %s failed (%s); resuming in %ss, attempt %s",
        seat,
        conversation_id,
        found.kind,
        round(wait or 0),
        attempt,
    )
    return stored


async def _record_schedule(
    session: AsyncSession, row: TurnResume, now: datetime
) -> tuple[dict, uuid.UUID | None] | None:
    """Keep the recovery outcome in the transaction that schedules it."""
    dropped = row.dropped_at is not None
    if dropped:
        key = (
            "turnResumeExhausted"
            if row.dropped_reason == ATTEMPTS_USED_UP
            else "turnResumeNoReset"
        )
        content = say(key)
    else:
        minutes = max(1, math.ceil((row.due_at - now).total_seconds() / 60))
        content = (
            # The upgrade's line names whose work picks up again.
            say(
                _WAIT_KEYS[row.reason],
                minutes=minutes,
                agent=f"<@{row.recipient_handle}>",
            )
            if row.reason == "runner_replaced"
            else say(_WAIT_KEYS[row.reason], minutes=minutes)
        )
    kept = await record(
        session,
        conversation_id=row.conversation_id,
        content=content,
        meta={
            **notice(
                EVENT_TURN_RESUME_STOPPED if dropped else EVENT_TURN_RESUME_WAIT,
                severity=SEVERITY_WARN,
                who=WHO_PLATFORM,
            ),
            "reason": row.dropped_reason if dropped else row.reason,
            "resume_id": str(row.id),
        },
        turn_id=row.failed_turn_id,
        seat=row.recipient_handle,
    )
    if kept is None:
        return None
    return as_payload(kept), await room_of_thread(session, row.conversation_id)


_WAIT_KEYS: Final = {
    "rate_limited": "turnResumeWaitRateLimited",
    "overloaded": "turnResumeWaitOverloaded",
    "stream_ended": "turnResumeWaitStreamEnded",
    "unreachable": "turnResumeWaitUnreachable",
    "egress_offline": "turnResumeWaitEgressOffline",
    "subscription_cooling": "turnResumeWaitSubscriptionCooling",
    "quota_reset": "turnResumeWaitQuotaReset",
    "runner_replaced": "turnResumeWaitRunnerReplaced",
}


async def started_on_receipt(
    session: AsyncSession, delivery_id: uuid.UUID | None, attempt_id: uuid.UUID | None
) -> tuple[dict, uuid.UUID | None] | None:
    """Record a resume only when the session echoes its instruction.

    The receipt transaction owns the delivery lock. Replayed echoes find the
    existing record, so the history keeps one start for this attempt.
    """
    if delivery_id is None or attempt_id is None:
        return None
    row = await session.scalar(
        select(TurnResume)
        .join(Delivery, Delivery.event_id == TurnResume.id)
        .where(Delivery.id == delivery_id, Delivery.attempt_id == attempt_id)
    )
    if row is None:
        return None
    existing = await session.scalar(
        select(RunRecord.id).where(
            RunRecord.conversation_id == row.conversation_id,
            RunRecord.kind == EVENT_TURN_RESUME_STARTED,
            RunRecord.meta["resume_id"].as_string() == str(row.id),
        )
    )
    if existing is not None:
        return None
    kept = await record(
        session,
        conversation_id=row.conversation_id,
        content=say("turnResumeStarted"),
        meta={
            **notice(
                EVENT_TURN_RESUME_STARTED, severity=SEVERITY_INFO, who=WHO_PLATFORM
            ),
            "resume_id": str(row.id),
        },
        turn_id=attempt_id,
        seat=row.recipient_handle,
    )
    if kept is None:
        return None
    return as_payload(kept), await room_of_thread(session, row.conversation_id)


async def started_record(
    session: AsyncSession, delivery_id: uuid.UUID | None, attempt_id: uuid.UUID | None
) -> tuple[dict, uuid.UUID | None] | None:
    """The record ``started_on_receipt`` kept for this attempt, as it is told:
    for telling it again when what followed its commit was not all done."""
    if delivery_id is None or attempt_id is None:
        return None
    row = await session.scalar(
        select(TurnResume)
        .join(Delivery, Delivery.event_id == TurnResume.id)
        .where(Delivery.id == delivery_id, Delivery.attempt_id == attempt_id)
    )
    if row is None:
        return None
    kept = await session.scalar(
        select(RunRecord).where(
            RunRecord.conversation_id == row.conversation_id,
            RunRecord.kind == EVENT_TURN_RESUME_STARTED,
            RunRecord.meta["resume_id"].as_string() == str(row.id),
        )
    )
    if kept is None:
        return None
    return as_payload(kept), await room_of_thread(session, row.conversation_id)


async def report_failed_deliveries(sessions: SessionFactory) -> int:
    """Report resume instructions the delivery ledger can no longer start."""
    reported: list[tuple[dict, uuid.UUID | None]] = []
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(TurnResume)
                .join(Delivery, Delivery.event_id == TurnResume.id)
                .where(
                    Delivery.state == "failed",
                    TurnResume.handed_at.is_not(None),
                    TurnResume.dropped_at.is_(None),
                )
                .limit(100)
                .with_for_update(of=TurnResume, skip_locked=True)
            )
        )
        for row in rows:
            room = await room_of(session, row.conversation_id)
            row.dropped_at = _now()
            row.dropped_reason = DELIVERY_FAILED
            kept = await record(
                session,
                conversation_id=row.conversation_id,
                content=say("turnResumeDeliveryFailed"),
                meta={
                    **notice(
                        EVENT_TURN_RESUME_STOPPED,
                        severity=SEVERITY_WARN,
                        who=WHO_PLATFORM,
                    ),
                    "resume_id": str(row.id),
                    "reason": DELIVERY_FAILED,
                },
                turn_id=row.failed_turn_id,
                seat=row.recipient_handle,
            )
            await announce(
                session,
                place_id=room,
                task_id=row.conversation_id if room != row.conversation_id else None,
                content=say("turnResumeDeliveryFailed"),
                turn_id=row.failed_turn_id,
                meta={
                    **notice(
                        EVENT_TURN_FAILED,
                        severity=SEVERITY_ERROR,
                        who=WHO_HUMAN,
                        retryable=True,
                    ),
                    "resume_id": str(row.id),
                    "seat": row.recipient_handle,
                },
            )
            if kept is not None:
                reported.append(
                    (
                        as_payload(kept),
                        await room_of_thread(session, row.conversation_id),
                    )
                )
        await session.commit()
    for payload, room in reported:
        await publish(payload, room=room)
    return len(rows)


def _wait(
    found: Resumable, attempt: int, now: datetime
) -> tuple[float | None, str | None]:
    """Seconds to wait before the resume, or the reason there is none."""
    policy = found.policy
    if policy.platform:
        return policy.base_s, None
    if attempt > policy.attempts:
        return None, ATTEMPTS_USED_UP
    stated = found.wait_s
    if stated is None and found.kind == SUBSCRIPTION_COOLING:
        pool = read_claude_pool(snapshot_path(), now=now.timestamp())
        stated = pool["retry_after"]
        if stated is None and pool["reason"] is None and pool["accounts"]:
            states = {row["state"] for row in pool["accounts"]}
            if "cooling" not in states and "available" not in states:
                # Every account is spent with no reset to wait for: an
                # operator resets them, or someone moves the project off the
                # subscription. Neither is the platform's to do.
                return None, NO_RESET
    if stated is not None:
        # Spread the seats that waited on the same reset over a short span.
        return stated + _AFTER_RESET_S + random.uniform(
            0, min(60.0, stated * 0.1)
        ), None
    if policy.needs_reset:
        return None, NO_RESET
    backoff = min(policy.cap_s, policy.base_s * 2 ** (attempt - 1))
    return backoff * random.uniform(0.8, 1.2), None


async def _streak(
    session: AsyncSession, conversation_id: uuid.UUID, seat: str, now: datetime
) -> int:
    """How many resumes in a row this seat already had: the previous row's
    attempt when this failure continues its streak, else 0."""
    previous = await session.scalar(
        select(TurnResume)
        .where(
            TurnResume.conversation_id == conversation_id,
            TurnResume.recipient_handle == seat,
        )
        .order_by(TurnResume.failed_at.desc())
        .limit(1)
    )
    if previous is None:
        return 0
    if previous.dropped_reason in (
        PERSON_SPOKE,
        SEAT_RAN_AGAIN,
        CLOSED,
        SEAT_GONE,
        DELIVERY_FAILED,  # no resumed turn started; a later manual retry is a new streak
    ):
        return 0
    since = previous.handed_at or previous.failed_at
    if now - since > STREAK_WINDOW:
        return 0
    if await _person_spoke(session, conversation_id, previous.failed_at):
        return 0
    return previous.attempt


async def hand_due(
    sessions: SessionFactory,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Hand every due resume to its seat through the delivery ledger, or drop
    it with the reason it no longer applies; how many were handed over. The
    timed-delivery clock dispatches them (`delivery.timer.deliver_due`)."""
    now = now or _now()
    handed = 0
    stopped: list[tuple[dict, uuid.UUID | None]] = []
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(TurnResume)
                .where(_owed(), TurnResume.due_at <= now)
                .order_by(TurnResume.due_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        rooms = await rooms_of(session, [row.conversation_id for row in rows])
        for row in rows:
            room = rooms[row.conversation_id]
            dropped = await _no_longer_applies(session, row, room)
            if dropped is not None:
                row.dropped_at = now
                row.dropped_reason = dropped
                kept = await record(
                    session,
                    conversation_id=row.conversation_id,
                    content=say("turnResumeCanceled"),
                    meta={
                        **notice(
                            EVENT_TURN_RESUME_STOPPED,
                            severity=SEVERITY_WARN,
                            who=WHO_PLATFORM,
                        ),
                        "resume_id": str(row.id),
                        "reason": dropped,
                    },
                    turn_id=row.failed_turn_id,
                    seat=row.recipient_handle,
                )
                if kept is not None:
                    stopped.append(
                        (
                            as_payload(kept),
                            await room_of_thread(session, row.conversation_id),
                        )
                    )
                logger.info(
                    "turn resume: %s in %s not resumed: %s",
                    row.recipient_handle,
                    row.conversation_id,
                    dropped,
                )
                continue
            await record_agent(
                session,
                DeliveryEvent(
                    # The resume is its own event: the dedup key follows it, so
                    # however often this runs, the ledger holds one line.
                    id=row.id,
                    type=NotificationType.ROOM_NOTICE,
                    payload={
                        "projectId": str(row.project_id),
                        "topicId": str(room),
                        "eventType": EVENT_TURN_FAILED,
                        "severity": SEVERITY_WARN,
                    },
                    occurred_at=row.failed_at,
                ),
                conversation_id=row.conversation_id,
                instance_id=row.agent_instance_id,
                content=str(RESUME_NOTE),
            )
            row.handed_at = now
            handed += 1
            logger.info(
                "turn resume: handed %s in %s its resume (%s, attempt %s)",
                row.recipient_handle,
                row.conversation_id,
                row.reason,
                row.attempt,
            )
        await session.commit()
    for payload, room in stopped:
        await publish(payload, room=room)
    return handed


async def _no_longer_applies(
    session: AsyncSession, row: TurnResume, room: uuid.UUID
) -> str | None:
    if not await _open(session, row.conversation_id, room):
        return CLOSED
    instance = await session.get(AgentInstance, row.agent_instance_id)
    if instance is None or not instance.is_active:
        return SEAT_GONE
    if await _person_spoke(session, row.conversation_id, row.failed_at):
        return PERSON_SPOKE
    ran = await session.scalar(
        select(AgentTurn.id)
        .where(
            AgentTurn.conversation_id == row.conversation_id,
            # A turn row names the agent by its handle in the project (the
            # key its session is kept under) or, on some paths (the orphan
            # re-send), by the seat; either may be there (`ask.py` reads both).
            AgentTurn.agent_handle.in_((instance.handle, row.recipient_handle)),
            AgentTurn.started_at > row.failed_at,
        )
        .limit(1)
    )
    return SEAT_RAN_AGAIN if ran is not None else None


async def _person_spoke(
    session: AsyncSession, conversation_id: uuid.UUID, since: datetime
) -> bool:
    """Whether a person (not an agent, not the platform) has said anything in
    this conversation since ``since``."""
    said = await session.scalar(
        select(Block.id)
        .where(
            Block.conversation_id == conversation_id,
            Block.created_at > since,
            Block.author_type == AuthorType.participant,
            Block.kind.in_((BlockKind.message, BlockKind.attachment)),
            ~agent_handle_column(Block.author),
        )
        .limit(1)
    )
    return said is not None


async def _open(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    room_id: uuid.UUID | None = None,
) -> bool:
    """Whether work can still happen here: an open task in a live room, or a
    room that is not archived. ``room_id`` is the conversation's room when the
    caller has read it already (`hand_due` reads a whole batch's at once)."""
    task = await session.get(Task, conversation_id)
    if task is not None:
        if task.status != TaskStatus.open:
            return False
        room = await session.get(Topic, task.room_id)
    else:
        room = await session.get(
            Topic, room_id or await room_of(session, conversation_id)
        )
    return room is not None and room.status != TopicStatus.archived
