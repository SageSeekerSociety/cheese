"""Durable, explicitly addressed inputs for native agent sessions.

The ledger owns intent; the runner owns admission. A transport receipt ends
delivery independently of model-work completion. An interrupted sending attempt
is uncertain, never permission to inject the instruction a second time.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ValidationError
from app.domain.agent_instance.models import AgentInstance
from app.domain.delivery.ledger import DeliveryEvent, dedup_key
from app.domain.delivery.models import Delivery, TimedDelivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

LEASE_SECONDS = 120
RETRY_SECONDS = 30


def now():
    return datetime.now(UTC)


async def instance_for_seat(session, project_id, seat):
    instances = await session.scalars(
        select(AgentInstance).where(AgentInstance.project_id == project_id)
    )
    return next(
        (row for row in instances if agent_instance_handle(row.id) == seat), None
    )


async def record_agent(
    session, event: DeliveryEvent, *, topic_id, instance_id, content
):
    """Record one event/recipient in the producer's transaction, without I/O."""
    seat = agent_instance_handle(instance_id)
    payload = {**event.payload, "content": content}
    await session.execute(
        insert(Delivery)
        .values(
            id=uuid.uuid4(),
            event_id=event.id,
            recipient_handle=seat,
            receiver_id=None,
            agent_instance_id=instance_id,
            topic_id=topic_id,
            dedup_key=dedup_key(event.id, seat),
            type=event.type.value,
            payload=payload,
            event_at=event.occurred_at,
            recorded_at=now(),
            state="pending",
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
    )


async def record_task_instruction(
    session, event: DeliveryEvent, *, task: Task, content: str
):
    """Keep a card instruction even before a worker's parent is known."""
    instance_id = task.execution_agent_instance_id
    seat = agent_instance_handle(instance_id) if instance_id else ""
    await session.execute(
        insert(Delivery)
        .values(
            id=uuid.uuid4(),
            event_id=event.id,
            recipient_handle=seat,
            receiver_id=None,
            agent_instance_id=instance_id,
            topic_id=task.room_id,
            task_id=task.id,
            dedup_key=f"{event.id}:task-parent",
            type=event.type.value,
            payload={
                **event.payload,
                "content": content,
                "worker_id": task.subagent_id,
                "parent_session_id": task.execution_parent_session_id,
                "parent_turn_id": str(task.execution_turn_id)
                if task.execution_turn_id
                else None,
            },
            event_at=event.occurred_at,
            recorded_at=now(),
            state="pending",
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
    )


async def dispatch_pending(sessions, *, chat, runner, limit=100):
    """Claim committed intent; expired sending attempts require reconciliation."""
    stamp = now()
    claimed = []
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(Delivery)
                .where(
                    or_(
                        Delivery.agent_instance_id.is_not(None),
                        Delivery.task_id.is_not(None),
                    ),
                    Delivery.sent_at.is_(None),
                    Delivery.state.in_(("pending", "claimed", "sending")),
                    or_(Delivery.lease_until.is_(None), Delivery.lease_until <= stamp),
                    or_(Delivery.retry_at.is_(None), Delivery.retry_at <= stamp),
                )
                .order_by(Delivery.recorded_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            if row.state == "sending":
                row.state = "uncertain"
                row.last_error = "Sender stopped before recording the receiver result"
                continue
            if row.task_id is not None:
                # A later parent turn can resume the same child. Its turn ID is
                # hook provenance, not a different execution target.
                task = await session.get(Task, row.task_id)
                if task is None or task.status != TaskStatus.open:
                    row.state = "failed"
                    row.last_error = "The task is no longer open"
                    continue
                if row.agent_instance_id is None:
                    if task.execution_agent_instance_id is None:
                        row.last_error = (
                            "Waiting for the task's observed execution parent"
                        )
                        row.retry_at = stamp + timedelta(seconds=RETRY_SECONDS)
                        continue
                    row.agent_instance_id = task.execution_agent_instance_id
                    row.recipient_handle = agent_instance_handle(row.agent_instance_id)
                    row.payload = {
                        **row.payload,
                        "worker_id": task.subagent_id,
                        "parent_session_id": task.execution_parent_session_id,
                        "parent_turn_id": str(task.execution_turn_id),
                    }
                if (
                    task.execution_agent_instance_id != row.agent_instance_id
                    or task.subagent_id != row.payload.get("worker_id")
                    or task.execution_parent_session_id
                    != row.payload.get("parent_session_id")
                ):
                    row.state = "failed"
                    row.last_error = (
                        "The addressed worker was replaced; "
                        "a new instruction must name its replacement"
                    )
                    continue
            topic = await session.get(Topic, row.topic_id)
            agent = await session.get(AgentInstance, row.agent_instance_id)
            if (
                topic is None
                or topic.status == TopicStatus.archived
                or agent is None
                or not agent.is_active
                or agent.project_id != topic.project_id
                or row.recipient_handle
                not in await TopicMemberService(session).agent_handles(topic.id)
            ):
                row.state = "failed"
                row.last_error = "Recipient no longer has an active seat in this room"
                continue
            row.state = "claimed"
            row.attempt_id = uuid.uuid4()
            row.lease_until = stamp + timedelta(seconds=LEASE_SECONDS)
            row.attempts += 1
            content = row.payload["content"]
            if row.task_id is not None:
                content += (
                    f"\nExecution target: task={row.task_id}; "
                    f"native child={row.payload.get('worker_id')}; "
                    f"parent session={row.payload.get('parent_session_id')}. "
                    "Apply child control only to this worker in this parent session; "
                    "if it has been replaced or cannot be identified, "
                    "report that instead of controlling another child."
                )
            claimed.append(
                (
                    row.id,
                    row.attempt_id,
                    row.topic_id,
                    row.agent_instance_id,
                    row.recipient_handle,
                    content,
                )
            )
        await session.commit()
    from app.domain.agent.runtime import addressed_to_agent

    for delivery_id, attempt, topic_id, instance_id, seat, content in claimed:
        runner.submit(
            chat,
            topic_id,
            author="system",
            content=content,
            addressed=addressed_to_agent(seat),
            turn_id=attempt,
            delivery_id=delivery_id,
            recipient_instance_id=instance_id,
        )
    return len(claimed)


async def run_attempt(sessions, delivery_id, attempt_id, work):
    """Renew admission ownership while queued; settle only this claimed attempt."""

    async def renew():
        while True:
            await asyncio.sleep(LEASE_SECONDS / 3)
            async with sessions() as session:
                await session.execute(
                    update(Delivery)
                    .where(
                        Delivery.id == delivery_id,
                        Delivery.attempt_id == attempt_id,
                        Delivery.state.in_(("claimed", "sending")),
                    )
                    .values(lease_until=now() + timedelta(seconds=LEASE_SECONDS))
                )
                await session.commit()

    heartbeat = asyncio.create_task(renew())
    try:
        await work
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        async with sessions() as session:
            row = await session.scalar(
                select(Delivery)
                .where(
                    Delivery.id == delivery_id,
                    Delivery.attempt_id == attempt_id,
                )
                .with_for_update()
            )
            if row is not None:
                if row.state == "claimed":
                    row.state = "pending"
                    row.retry_at = now() + timedelta(seconds=RETRY_SECONDS)
                    row.last_error = (
                        "Input was not dispatched; "
                        "admission or preparation did not complete"
                    )
                elif row.state == "sending":
                    row.state = "uncertain"
                    row.last_error = (
                        "Receiver result was not confirmed; "
                        "automatic replay is withheld"
                    )
                row.lease_until = None
            await session.commit()


async def begin_send(sessions, delivery_id, attempt_id, *, parent_session_id=None):
    """Fence stale queued runners immediately before they contact the receiver."""
    async with sessions() as session:
        row = await session.scalar(
            select(Delivery)
            .where(
                Delivery.id == delivery_id,
                Delivery.attempt_id == attempt_id,
            )
            .with_for_update()
        )
        if row is not None and row.task_id is not None:
            task = await session.get(Task, row.task_id)
            if (
                task is None
                or task.status != TaskStatus.open
                or task.execution_agent_instance_id != row.agent_instance_id
                or task.subagent_id != row.payload.get("worker_id")
                or task.execution_parent_session_id
                != row.payload.get("parent_session_id")
                or (
                    row.payload.get("parent_session_id") is not None
                    and parent_session_id != row.payload["parent_session_id"]
                )
            ):
                row.state = "failed"
                row.last_error = (
                    "The target worker or native parent changed before delivery"
                )
                await session.commit()
                raise ValidationError(row.last_error)
        result = await session.execute(
            update(Delivery)
            .where(
                Delivery.id == delivery_id,
                Delivery.attempt_id == attempt_id,
                Delivery.state == "claimed",
                Delivery.lease_until > now(),
            )
            .values(state="sending")
            .returning(Delivery.id)
        )
        if result.scalar_one_or_none() is None:
            raise ValidationError("Delivery attempt no longer owns this input")
        await session.commit()


async def receive_attempt(session, attempt_id, stamp):
    """Commit the transport receipt with the existing turn receipt."""
    events = list(
        (
            await session.scalars(
                update(Delivery)
                .where(
                    Delivery.attempt_id == attempt_id,
                    Delivery.state.in_(("sending", "uncertain")),
                )
                .values(
                    state="received", sent_at=stamp, lease_until=None, last_error=None
                )
                .returning(Delivery.event_id)
            )
        ).all()
    )
    if events:
        await session.execute(
            update(TimedDelivery)
            .where(
                TimedDelivery.event_id.in_(events),
                TimedDelivery.delivered_at.is_(None),
            )
            .values(delivered_at=stamp)
        )
