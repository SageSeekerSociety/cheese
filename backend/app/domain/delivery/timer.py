"""Participant-requested future delivery, materialized before dispatch.

A due event and its recipient commit together. Model admission and transport
receipt happen later; only the latter completes an agent's scheduled delivery.
Human recipients use the same durable mailbox ledger as other notifications.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.errors import ValidationError
from app.core.live_frames import show_state_once_committed
from app.core.sentences import say
from app.domain.agent.platform_notices import (
    EVENT_TIMED_DELIVERY,
    EVENT_TURN_FAILED,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    WHO_CHEESE,
    WHO_HUMAN,
    notice,
)
from app.domain.conversation.services import room_of
from app.domain.delivery.agent import (
    GAVE_UP,
    GIVE_UP_AFTER,
    dispatch_pending,
    instance_for_seat,
    record_agent,
)
from app.domain.delivery.ledger import DeliveryEvent, Ledger
from app.domain.delivery.models import Delivery, TimedDelivery
from app.domain.identity.arrival import Arrival, how_it_arrives
from app.domain.notification.models import NotificationType
from app.domain.notification.publisher import build_notification_event_handler
from app.domain.room_task.closing import CLOSES_TASK, close_after_summary
from app.domain.run_record.service import keep as keep_record
from app.domain.topic.models import Topic
from app.domain.user.services import user_by_handle

DELIVERED_AS_ASKED = say("timedDelivery")


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def deliver_at(
    session: AsyncSession,
    *,
    when: datetime,
    event: str,
    recipient: str,
    conversation_id: uuid.UUID,
    project_id: uuid.UUID,
) -> TimedDelivery:
    if when.tzinfo is None:
        raise ValidationError(say("deliveryTimeNeedsZone"))
    if not event.strip():
        raise ValidationError(say("deliveryContentEmpty"))
    agent_id = receiver_id = None
    if how_it_arrives(recipient) is Arrival.turn:
        agent = await instance_for_seat(session, project_id, recipient)
        if agent is None or not agent.is_active:
            raise ValidationError(
                "The requested recipient is not an active project agent"
            )
        agent_id = agent.id
    else:
        person = await user_by_handle(session, recipient)
        if person is None:
            raise ValidationError("The requested recipient has no mailbox")
        receiver_id = person.id
    row = TimedDelivery(
        id=uuid.uuid4(),
        project_id=project_id,
        conversation_id=conversation_id,
        recipient_handle=recipient,
        agent_instance_id=agent_id,
        receiver_id=receiver_id,
        content=event,
        due_at=when,
        requested_at=_utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def _hand_to_agent(session: AsyncSession, row: TimedDelivery) -> None:
    """The ledger hands the event to the agent's seat. The conversation is not
    told: the agent asked for it, and nobody else is waiting on it. It is kept
    as a run record under the delivery's own id, which the ledger's event
    carries, so the 现场 shows the turn it started."""
    await keep_record(
        session,
        project_id=row.project_id,
        conversation_id=row.conversation_id,
        content=DELIVERED_AS_ASKED,
        meta=notice(
            EVENT_TIMED_DELIVERY,
            severity=SEVERITY_INFO,
            who=WHO_CHEESE,
            detail=row.content,
            detail_label=say("labelTimedDeliveryNote"),
        ),
        record_id=row.id,
    )
    assert row.agent_instance_id is not None
    await record_agent(
        session,
        _event(
            row,
            room_id=await room_of(session, row.conversation_id),
            content=row.content,
        ),
        conversation_id=row.conversation_id,
        instance_id=row.agent_instance_id,
        content=row.content,
    )


async def _hand_to_person(session: AsyncSession, row: TimedDelivery) -> None:
    """A person's reminder reaches their own inbox (and push), and nobody else.

    It does not land in the room. The timeline is read by everyone in the room
    and says 「你」 to each of them; a reminder someone set for themselves is
    theirs alone, the way a reminder in any chat app is. The notification links
    back to the room it was set in.
    """
    assert row.receiver_id is not None
    said = say("reminder", text=row.content)
    room_id = await room_of(session, row.conversation_id)
    topic = await session.get(Topic, room_id)
    event = _event(
        row,
        room_id=room_id,
        content=str(said),
        message=said.descriptor(),
        **({"topicTitle": topic.title} if topic is not None else {}),
    )
    ledger = Ledger(session)
    pending = await ledger.record_mailbox(event, row.recipient_handle, row.receiver_id)
    if pending is not None:
        await ledger.send([pending], build_notification_event_handler(session))


def _event(
    row: TimedDelivery, *, room_id: uuid.UUID, content: str, **extra: object
) -> DeliveryEvent:
    return DeliveryEvent(
        id=row.id,
        type=NotificationType.ROOM_NOTICE,
        payload={
            "projectId": str(row.project_id),
            "topicId": str(room_id),
            "content": content,
            "eventType": EVENT_TIMED_DELIVERY,
            "severity": SEVERITY_INFO,
            **extra,
        },
        occurred_at=row.due_at,
    )


async def deliver_due(
    sessions: SessionFactory, *, chat, runner, limit: int = 100
) -> dict[str, int]:
    stamp = _utcnow()
    materialized = 0
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(TimedDelivery)
                .where(
                    TimedDelivery.materialized_at.is_(None),
                    TimedDelivery.delivered_at.is_(None),
                    TimedDelivery.due_at <= stamp,
                )
                .order_by(TimedDelivery.due_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            # Rows predating identity snapshots are resolved once, while still
            # pending. A completed legacy request is never selected or replayed.
            if row.agent_instance_id is None and row.receiver_id is None:
                if how_it_arrives(row.recipient_handle) is Arrival.turn:
                    agent = await instance_for_seat(
                        session, row.project_id, row.recipient_handle
                    )
                    if agent is None:
                        continue
                    row.agent_instance_id = agent.id
                else:
                    person = await user_by_handle(session, row.recipient_handle)
                    if person is None:
                        continue
                    row.receiver_id = person.id
            row.event_id = row.id
            row.materialized_at = stamp
            if row.agent_instance_id is not None:
                await _hand_to_agent(session, row)
            else:
                await _hand_to_person(session, row)
            materialized += 1
        await session.commit()
    await give_up_stale(sessions, chat=chat)
    # Post-commit dispatch is optional for recovery: every scan also claims older
    # committed intent, including events created by a process that then stopped.
    dispatched = await dispatch_pending(sessions, chat=chat, runner=runner, limit=limit)
    return {"materialized": materialized, "dispatched": dispatched}


async def give_up_stale(sessions: SessionFactory, *, chat) -> int:
    """Fail every instruction to an AI teammate that has not started half an
    hour after it was given (`agent.GIVE_UP_AFTER`), and say so once in each
    conversation where a person's message went unanswered: whoever waits there
    sees it, and 「重试」 starts a turn again. Returns how many gave up."""
    stamp = _utcnow()
    told: dict[uuid.UUID, str] = {}
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(Delivery)
                .where(
                    Delivery.agent_instance_id.is_not(None),
                    Delivery.sent_at.is_(None),
                    Delivery.state.in_(("pending", "claimed")),
                    Delivery.attempts > 0,
                    Delivery.recorded_at < stamp - GIVE_UP_AFTER,
                    or_(Delivery.lease_until.is_(None), Delivery.lease_until <= stamp),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            row.state = "failed"
            row.last_error = GAVE_UP
            # A finished task waiting on this turn to be written up closes
            # without it.
            if (row.payload or {}).get(CLOSES_TASK) and row.conversation_id:
                closed = await close_after_summary(session, row.conversation_id)
                if closed is not None:
                    show_state_once_committed(session, closed.room_id)
            # Only a person's message is told: 「重试」 starts a turn from the
            # messages still waiting, and a platform instruction is not one. A
            # task's opening says it failed on the task page; a routine's run
            # on its own line.
            if (
                row.conversation_id is not None
                and row.type == NotificationType.MENTION.value
            ):
                told.setdefault(row.conversation_id, row.recipient_handle)
        await session.commit()
    for conversation_id, seat in told.items():
        await chat.post_system_event(
            conversation_id,
            say("deliveryGaveUp", agent=f"<@{seat}>"),
            meta=notice(
                EVENT_TURN_FAILED,
                severity=SEVERITY_ERROR,
                who=WHO_HUMAN,
                retryable=True,
            ),
        )
    return len(rows)
