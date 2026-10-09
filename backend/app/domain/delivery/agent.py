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
from app.core.live_frames import show_state_once_committed
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.conversation.services import project_of, room_of
from app.domain.delivery.ledger import DeliveryEvent, dedup_key
from app.domain.delivery.models import Delivery, NativeInput, TimedDelivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.models import Project
from app.domain.room_task.closing import CLOSES_TASK, close_after_summary
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

LEASE_SECONDS = 120
# An attempt that was not admitted waits before the next one, twice as long each
# time, up to the cap. A fixed short wait turned every permanent refusal into a
# loop: each retry is a whole turn that takes the seat and starts the session,
# so a room with a dozen refused deliveries ran a turn every two seconds and
# the people in it queued behind those (2026-10-06, 1278 in an hour).
RETRY_SECONDS = 30
RETRY_CAP_SECONDS = 300
# An instruction that has not started anywhere half an hour after it was given
# is not going to: its computer is gone, or the agent cannot be seated. It
# fails, so whoever is waiting sees why and can try again, instead of watching
# it retry every five minutes for ever.
GIVE_UP_AFTER = timedelta(minutes=30)
GAVE_UP = "Not started within 30 minutes of being given"


def retry_after(attempts: int) -> float:
    """How long a delivery waits after its ``attempts``-th unadmitted attempt."""
    return min(RETRY_SECONDS * 2 ** max(0, attempts - 1), RETRY_CAP_SECONDS)


class DeliveryTargetChanged(ValidationError):
    """The owned attempt has a confirmed invalid target; commit its failure."""


def now():
    return datetime.now(UTC)


def work_interval_is_over():
    """Whether this input's own work interval is one the platform ended.

    A correlated EXISTS over the turn intervals, anchored on the input row that
    names its work (``work_id``/``conversation_id``). The fact belongs to the agent
    domain's turn intervals, and that domain already imports this one (it reads
    input holds), so the question goes out through this seam — importing
    the turn models on the delivery side would close a domain cycle (C3 in
    backend/.importlinter).
    """
    # deferred-import: breaks the cycle agent.runtime -> delivery.agent
    from app.domain.agent.runtime import AgentTurnRepository

    return AgentTurnRepository.interval_is_over(
        turn_id=NativeInput.work_id, topic_id=NativeInput.conversation_id
    )


async def instance_for_seat(session, project_id, seat):
    instances = await session.scalars(
        select(AgentInstance).where(AgentInstance.project_id == project_id)
    )
    return next(
        (row for row in instances if agent_instance_handle(row.id) == seat), None
    )


async def record_agent(
    session, event: DeliveryEvent, *, conversation_id, instance_id, content
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
            conversation_id=conversation_id,
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

    Addressed to the conversation, not the room: ``conversation_id`` is the task's
    id, which is what the runner runs a turn in, and the agent is the one working
    the task (its own pick, else its room's) — the one that answers there.
    """
    # deferred-import: Topic is bound at the top of this module already
    from app.domain.topic.models import Topic

    project = await session.get(Project, task.project_id)
    room = await session.get(Topic, task.room_id)
    if project is None or room is None:
        return
    agent = await AgentInstanceService(session).for_task(
        project, room, task.agent_handle
    )
    await session.execute(
        insert(Delivery)
        .values(
            id=uuid.uuid4(),
            event_id=event.id,
            recipient_handle=agent_instance_handle(agent.instance_id),
            receiver_id=None,
            agent_instance_id=agent.instance_id,
            conversation_id=task.id,
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
                    Delivery.agent_instance_id.is_not(None),
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
            if row.attempts > 0 and stamp - row.recorded_at > GIVE_UP_AFTER:
                # Not tried again; the sweep fails it and says so where people
                # wait (`timer.give_up_stale`).
                continue
            agent = await session.get(AgentInstance, row.agent_instance_id)
            task = await session.get(Task, row.conversation_id)
            if task is not None:
                # A task's own session: the task must still be open, and the
                # agent still the project's. It sits on no room's roster.
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
                # A room's own line, or one of its 支线: the roster is the room's.
                topic = await session.get(
                    Topic, await room_of(session, row.conversation_id)
                )
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
            row.state = "claimed"
            row.attempt_id = uuid.uuid4()
            row.lease_until = stamp + timedelta(seconds=LEASE_SECONDS)
            row.attempts += 1
            content = row.payload["content"]
            claimed.append(
                (
                    row.id,
                    row.attempt_id,
                    row.conversation_id,
                    row.agent_instance_id,
                    row.recipient_handle,
                    content,
                )
            )
        await session.commit()
    # deferred-import: breaks the cycle agent.runtime -> delivery.agent
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


#: The states of an input the session was never reached for: `claimed` is
#: between taking the row and fencing the send, `pending` is where an attempt
#: that never began the send puts it back. `sending` — and `uncertain` after it
#: — mean the session may have been reached, and the module's own rule holds:
#: never permission to inject the instruction a second time.
_NEVER_SENT = ("pending", "claimed")


async def redispatch_undelivered(
    sessions, *, conversation_id: uuid.UUID, chat, runner
) -> int:
    """Send this conversation, now, the inputs the platform still owes it.

    A delivery whose turn died before the session was reached goes back to
    ``pending`` with a backoff (``run_attempt``), and it waits for the next
    dispatch anyone happens to make in this project to pick it up — there is no
    such next dispatch in a room nobody else is doing anything in. A task told
    to start is the shape this has: the room shows that its session did not
    start, the card offers a retry, and the instruction sits on the ledger
    because the only way to send it again later is for something unrelated to
    happen first.

    Someone asking for that retry is asking for the turn now, which is what
    this does. Only rows the session was never reached for are taken, and only
    this conversation's: an interrupted sending attempt stays withheld
    (module docstring). How long an instruction given up on waits before its
    own sweep fails it is untouched (`GIVE_UP_AFTER`) — this moves the wait
    that a person asked to skip, not that one.
    """
    stamp = now()
    async with sessions() as session:
        owed = list(
            (
                await session.scalars(
                    select(Delivery.id).where(
                        Delivery.conversation_id == conversation_id,
                        Delivery.agent_instance_id.is_not(None),
                        Delivery.state.in_(_NEVER_SENT),
                        Delivery.sent_at.is_(None),
                        Delivery.attempts > 0,
                        or_(
                            Delivery.lease_until.is_(None),
                            Delivery.lease_until <= stamp,
                        ),
                    )
                )
            ).all()
        )
        if not owed:
            return 0
        # The backoff is the only thing holding these back, and it is a wait no
        # person asked for.
        await session.execute(
            update(Delivery).where(Delivery.id.in_(owed)).values(retry_at=None)
        )
        await session.commit()
    return await dispatch_pending(sessions, chat=chat, runner=runner, delivery_ids=owed)


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
                    row.retry_at = now() + timedelta(seconds=retry_after(row.attempts))
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
                # The turn that writes a finished task up is over, however it
                # went: the task closes now (`room_task.closing`).
                if row.state != "pending" and (row.payload or {}).get(CLOSES_TASK):
                    closed = await close_after_summary(session, row.conversation_id)
                    if closed is not None:
                        show_state_once_committed(session, closed.room_id)
            await session.commit()


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
    # The conversation it was addressed to must still be there, and a task's
    # must still be open.
    task = await session.get(Task, row.conversation_id)
    message = (
        "The task closed before delivery"
        if task is not None and task.status != TaskStatus.open
        else "The conversation is gone"
        if task is None and await project_of(session, row.conversation_id) is None
        else None
    )
    if message is not None:
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
