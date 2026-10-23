"""Recheck a turn's first input on its seat, after project admission."""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent.admission import Lease
from app.domain.agent.device_hub import device_hub
from app.domain.agent.owner_provider import owner_is_away
from app.domain.agent.pending_messages import defer_message
from app.domain.agent.queries import conversation_seat
from app.domain.agent.seat_admission import seat_admission
from app.domain.block.models import Block
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.notice_meta import (
    EVENT_TURN_FAILED,
    SEVERITY_INFO,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
from app.domain.room_task.place import PlaceResolver


class SeatResolver(Protocol):
    async def __call__(
        self,
        topic_id: uuid.UUID,
        *,
        user_block_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
    ) -> str: ...


class Draining(Protocol):
    """Which seats' runners are another release's, finishing their work
    (``ComputePool.draining``)."""

    def draining(self, topic_id: uuid.UUID, agent_handle: str) -> bool: ...

    def tell_draining(self, topic_id: uuid.UUID, agent_handle: str) -> bool: ...


class AdmissionEventWriter(Protocol):
    async def __call__(
        self,
        topic_id: uuid.UUID,
        content: str,
        /,
        *,
        meta: dict | None = None,
    ) -> dict | None: ...


@asynccontextmanager
async def admitted_initial(
    sessions: async_sessionmaker[AsyncSession],
    seat_lock_for: Callable[[uuid.UUID, str], Lease],
    resolve_seat: SeatResolver,
    post_event: AdmissionEventWriter,
    topic_id: uuid.UUID,
    delivery_id: uuid.UUID | None,
    *,
    draining: Draining,
    user_block_id: uuid.UUID | None = None,
    recipient_instance_id: uuid.UUID | None = None,
    recipient_handle: str | None = None,
) -> AsyncIterator[bool]:
    """Every initial turn rechecks durable ownership after project admission.

    Yields True when the seat still holds an input whose outcome is not settled,
    or its runner is another release's finishing its work (``draining``): the
    turn does not start, and a person's message waits for it instead. The wait
    for a drain is said in the room once per drain, so the person knows their
    message is not lost.

    The seat itself was taken before project admission, while this turn was on
    its way in (``RunnerIntake._admit``), so that a turn which cannot run yet
    never holds a place among the project's turns (#1561). The take below finds
    this task already inside that seat and yields to it rather than waiting
    behind itself; on any other path in, it is the take it always was.
    """
    instance_id = await seat_instance(sessions, delivery_id, recipient_instance_id)
    seat = await resolve_seat(
        topic_id,
        user_block_id=user_block_id,
        recipient_instance_id=instance_id,
        recipient_handle=recipient_handle,
    )
    async with seat_admission(seat_lock_for(topic_id, seat)):
        async with sessions() as session:
            place = await PlaceResolver(session).conversation(topic_id)
            # A room's addressed agent must still sit on its roster. A task's
            # agent is seated when the task is given to it, and the turn
            # refuses one that is not (`queries._acting_handle`).
            if instance_id is not None and place is not None and place.task is None:
                # deferred-import: tests patch app.domain.topic_membership.services
                from app.domain.topic_membership.services import TopicMemberService

                if agent_instance_handle(instance_id) not in await TopicMemberService(
                    session
                ).agent_handles(place.room_id):
                    raise ValidationError(
                        "The addressed agent is no longer seated in this room"
                    )
            seated = await conversation_seat(session, topic_id, seat)
            upgrading = draining.draining(topic_id, seat)
            pending = upgrading or (
                seated is not None
                and await seat_has_unfinished_input(session, topic_id, seated[1])
            )
            # A device-held connection needs its credential device online.
            # While it is away, the message waits; the periodic scan starts
            # waiting messages once the device comes back.
            away = (
                not pending
                and instance_id is not None
                and await owner_is_away(session, instance_id, device_hub)
            )
            told_away = told_upgrading = False
            if (pending or away) and user_block_id is not None:
                await defer_message(session, user_block_id)
                if away:
                    told_away = await _tell_once(session, user_block_id)
                if upgrading:
                    told_upgrading = draining.tell_draining(topic_id, seat)
            await session.commit()
        if told_away:
            await post_event(
                topic_id,
                say("ownerMachineAway"),
                meta=notice(EVENT_TURN_FAILED, severity=SEVERITY_INFO, who=WHO_HUMAN),
            )
        if told_upgrading:
            await post_event(
                topic_id,
                say("upgradeMessageWaits", agent=f"<@{seat}>"),
                meta=notice(
                    EVENT_TURN_FAILED, severity=SEVERITY_INFO, who=WHO_PLATFORM
                ),
            )
        yield pending or away


async def seat_instance(
    sessions: async_sessionmaker[AsyncSession],
    delivery_id: uuid.UUID | None,
    recipient_instance_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Which agent instance's seat a turn serializes on: the one its delivery
    named (a held message is addressed to an instance), the recipient's when
    the delivery is gone or the turn came in without one.

    Read by both places that have to agree on the seat — the take on the way in
    and the recheck after project admission. They read it separately rather
    than passing it along one of them: the second read is a delivery by primary
    key, and what it costs is worth less than the two having one answer.
    """
    if delivery_id is None:
        return recipient_instance_id
    async with sessions() as session:
        delivery = await session.get(Delivery, delivery_id)
    return delivery.agent_instance_id if delivery is not None else recipient_instance_id


async def _tell_once(session, block_id) -> bool:
    """Whether the room is still to be told this message waits for its
    agent's computer: once per message, not once per scan that finds it
    waiting."""

    block = await session.get(Block, block_id, with_for_update=True)
    if block is None or (block.meta or {}).get(_TOLD_AWAY):
        return False
    block.meta = {**(block.meta or {}), _TOLD_AWAY: True}
    return True


_TOLD_AWAY = "owner_away_told"
