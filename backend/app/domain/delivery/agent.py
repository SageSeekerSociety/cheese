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

from sqlalchemy import or_, select, true, update
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ValidationError
from app.domain.agent_instance.models import AgentInstance
from app.domain.delivery.ask_receipt_wait import (
    ASK_RECEIPT_WAIT,
    AskReceiptPending,
)
from app.domain.delivery.ask_session_wait import (
    ASK_SESSION_RETRY_SECONDS,
    ASK_SESSION_WAIT,
    ASK_SESSION_WAIT_REASON,
)
from app.domain.delivery.ledger import DeliveryEvent, dedup_key
from app.domain.delivery.models import Delivery, NativeInput, TimedDelivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

LEASE_SECONDS = 120
RETRY_SECONDS = 30


class DeliveryTargetChanged(ValidationError):
    """The owned attempt has a confirmed invalid target; commit its failure."""


def now():
    return datetime.now(UTC)


def work_interval_is_over():
    """Whether this input's own work interval is one the platform ended.

    A correlated EXISTS over the turn intervals, anchored on the input row that
    names its work (``work_id``/``topic_id``). The fact belongs to the agent
    domain's turn intervals, and that domain already imports this one (it reads
    answer ownership), so the question goes out through this seam — importing
    the turn models on the delivery side would close a domain cycle (C3 in
    backend/.importlinter).
    """
    from app.domain.agent.runtime import AgentTurnRepository

    return AgentTurnRepository.interval_is_over(
        turn_id=NativeInput.work_id, topic_id=NativeInput.topic_id
    )


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
    """Keep an instruction for a task's own session until it is delivered.

    Addressed to the conversation, not the room: ``topic_id`` is the task's id,
    which is what the runner runs a turn in, and the agent is the one working
    the task (its own pick, else the project's).
    """
    from app.domain.agent_instance.services import AgentInstanceService
    from app.domain.project.models import Project

    project = await session.get(Project, task.project_id)
    if project is None:
        return
    instance = await AgentInstanceService(session).for_handle(
        project, task.agent_handle
    )
    await session.execute(
        insert(Delivery)
        .values(
            id=uuid.uuid4(),
            event_id=event.id,
            recipient_handle=agent_instance_handle(instance.id),
            receiver_id=None,
            agent_instance_id=instance.id,
            topic_id=task.id,
            task_id=task.id,
            dedup_key=f"{event.id}:task",
            type=event.type.value,
            payload={**event.payload, "content": content},
            event_at=event.occurred_at,
            recorded_at=now(),
            state="pending",
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
    )


async def dispatch_pending(sessions, *, chat, runner, limit=100, delivery_ids=None):
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
                    Delivery.id.in_(delivery_ids)
                    if delivery_ids is not None
                    else true(),
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
            agent = await session.get(AgentInstance, row.agent_instance_id)
            if row.task_id is not None:
                # A task's own session: the task must still be open, and the
                # agent still the project's. It sits on no room's roster.
                task = await session.get(Task, row.task_id)
                topic = task and await session.get(Topic, task.room_id)
                if (
                    task is None
                    or task.status != TaskStatus.open
                    or topic is None
                    or topic.status == TopicStatus.archived
                    or agent is None
                    or not agent.is_active
                    or agent.project_id != task.project_id
                ):
                    row.state = "failed"
                    row.last_error = "The task is no longer open"
                    continue
            else:
                topic = await session.get(Topic, row.topic_id)
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
                    row.last_error = (
                        "Recipient no longer has an active seat in this room"
                    )
                    continue
            # A new attempt must not reuse an earlier receipt-wait marker.
            row.payload = {
                key: value
                for key, value in row.payload.items()
                if key != ASK_RECEIPT_WAIT
            }
            row.state = "claimed"
            row.attempt_id = uuid.uuid4()
            row.lease_until = stamp + timedelta(seconds=LEASE_SECONDS)
            row.attempts += 1
            content = row.payload["content"]
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


async def run_attempt(sessions, delivery_id, attempt_id, work, *, chat=None):
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
    waiting = None
    try:
        await work
    except AskReceiptPending as exc:
        waiting = exc
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
                    # An answer waiting for the conversation that asked is not a
                    # failed admission: it is admissible nowhere yet, and asking
                    # again in 30 s only spends attempts (``ask_session_wait``).
                    waiting_for_session = ASK_SESSION_WAIT in (row.payload or {})
                    row.retry_at = now() + timedelta(
                        seconds=(
                            ASK_SESSION_RETRY_SECONDS
                            if waiting_for_session
                            else RETRY_SECONDS
                        )
                    )
                    row.last_error = (
                        ASK_SESSION_WAIT_REASON
                        if waiting_for_session
                        else "Input was not dispatched; "
                        "admission or preparation did not complete"
                    )
                    if (
                        waiting is not None
                        and waiting.delivery_id == row.id
                        and waiting.attempt_id == row.attempt_id
                        and waiting.identity.topic_id == row.topic_id
                        and waiting.identity.recipient_handle == row.recipient_handle
                        and waiting.group_id == row.payload.get("ask_group")
                        and all(
                            (row.payload.get("ask_origin") or {}).get(field)
                            == getattr(waiting.identity, field)
                            for field in (
                                "recipient_handle",
                                "harness",
                                "native_session_id",
                            )
                        )
                    ):
                        row.payload = {
                            **row.payload,
                            ASK_RECEIPT_WAIT: waiting.marker(),
                        }
                        row.last_error = "Waiting for this group's native receipt"
                elif row.state == "sending":
                    row.state = "uncertain"
                    row.last_error = (
                        "Receiver result was not confirmed; "
                        "automatic replay is withheld"
                    )
                row.lease_until = None
            await session.commit()

    if waiting is not None and chat is not None:
        # Covers the receipt that committed before the wait marker was stored.
        chat.nudge_ask_receipts(waiting.identity)


async def begin_send(sessions, delivery_id, attempt_id):
    """Fence stale queued runners immediately before they contact the receiver."""
    rejected: DeliveryTargetChanged | None = None
    async with sessions() as session:
        try:
            await fence_send(session, delivery_id, attempt_id)
        except DeliveryTargetChanged as exc:
            rejected = exc
        await session.commit()
    if rejected is not None:
        raise rejected


async def fence_send(session, delivery_id, attempt_id):
    """Fence in the caller's identity-registration transaction; never commit here."""
    row = await session.scalar(
        select(Delivery)
        .where(
            Delivery.id == delivery_id,
            Delivery.attempt_id == attempt_id,
            Delivery.state == "claimed",
            Delivery.lease_until > now(),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValidationError("Delivery attempt no longer owns this input")
    if row.task_id is not None:
        task = await session.get(Task, row.task_id)
        if task is None or task.status != TaskStatus.open:
            message = "The task closed before delivery"
            result = await session.execute(
                update(Delivery)
                .where(
                    Delivery.id == delivery_id,
                    Delivery.attempt_id == attempt_id,
                    Delivery.state == "claimed",
                    Delivery.lease_until > now(),
                )
                .values(state="failed", last_error=message, lease_until=None)
                .returning(Delivery.id)
            )
            if result.scalar_one_or_none() is None:
                raise ValidationError("Delivery attempt no longer owns this input")
            raise DeliveryTargetChanged(message)
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
