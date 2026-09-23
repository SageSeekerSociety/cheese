"""Participant-requested future delivery, materialized before dispatch.

A due event and its recipient commit together. Model admission and transport
receipt happen later; only the latter completes an agent's scheduled delivery.
Human recipients use the same durable mailbox ledger as other notifications.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionFactory
from app.core.errors import ValidationError
from app.domain.agent.platform_notices import (
    EVENT_TIMED_DELIVERY,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.agent import dispatch_pending, instance_for_seat, record_agent
from app.domain.delivery.ledger import DeliveryEvent, Ledger
from app.domain.delivery.models import TimedDelivery
from app.domain.identity.arrival import Arrival, how_it_arrives
from app.domain.notification.models import NotificationType
from app.domain.notification.publisher import build_notification_event_handler
from app.domain.user.services import user_by_handle

DELIVERED_AS_ASKED = "你请平台在这个时刻把它递给你"


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def deliver_at(
    session: AsyncSession,
    *,
    when: datetime,
    event: str,
    recipient: str,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> TimedDelivery:
    if when.tzinfo is None:
        raise ValidationError("投递时刻要带时区")
    if not event.strip():
        raise ValidationError("要递的东西不能是空的")
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
        topic_id=topic_id,
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
            session.add(
                Block(
                    id=row.id,
                    project_id=row.project_id,
                    topic_id=row.topic_id,
                    author="system",
                    author_type=AuthorType.platform,
                    kind=BlockKind.event,
                    content=DELIVERED_AS_ASKED,
                    meta=notice(
                        EVENT_TIMED_DELIVERY,
                        severity=SEVERITY_INFO,
                        who=WHO_CHEESE,
                        detail=row.content,
                        detail_label="你当时写下的",
                    ),
                )
            )
            event = DeliveryEvent(
                id=row.id,
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "projectId": str(row.project_id),
                    "topicId": str(row.topic_id),
                    "content": row.content,
                    "eventType": EVENT_TIMED_DELIVERY,
                    "severity": SEVERITY_INFO,
                },
                occurred_at=row.due_at,
            )
            if row.agent_instance_id is not None:
                await record_agent(
                    session,
                    event,
                    topic_id=row.topic_id,
                    instance_id=row.agent_instance_id,
                    content=row.content,
                )
            else:
                assert row.receiver_id is not None
                ledger = Ledger(session)
                pending = await ledger.record_mailbox(
                    event, row.recipient_handle, row.receiver_id
                )
                if pending is not None:
                    await ledger.send(
                        [pending], build_notification_event_handler(session)
                    )
            materialized += 1
        await session.commit()
    # Post-commit dispatch is optional for recovery: every scan also claims older
    # committed intent, including events created by a process that then stopped.
    dispatched = await dispatch_pending(sessions, chat=chat, runner=runner, limit=limit)
    return {"materialized": materialized, "dispatched": dispatched}
