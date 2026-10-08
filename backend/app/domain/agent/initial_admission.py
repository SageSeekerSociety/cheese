"""Admit a turn's first input under its seat's lock, after project admission."""

from contextlib import asynccontextmanager

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent.device_hub import device_hub
from app.domain.agent.owner_provider import owner_is_away
from app.domain.agent.pending_messages import defer_message
from app.domain.agent.platform_notices import (
    EVENT_TURN_FAILED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.agent.queries import conversation_seat
from app.domain.agent.seat_admission import seat_admission
from app.domain.block.models import Block
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.room_task.place import PlaceResolver


@asynccontextmanager
async def admitted_initial(
    chat,
    topic_id,
    delivery_id,
    *,
    user_block_id=None,
    recipient_instance_id=None,
    recipient_handle=None,
):
    """Every initial turn rechecks durable ownership after project admission.

    Yields True when the seat still holds an input whose outcome is not settled:
    the turn does not start, and a person's message waits for it instead.
    """
    async with chat.session_factory() as session:
        delivery = (
            await session.get(Delivery, delivery_id)
            if delivery_id is not None
            else None
        )
        instance_id = (
            delivery.agent_instance_id
            if delivery is not None
            else recipient_instance_id
        )
    seat = await chat._turn_seat_handle(
        topic_id,
        user_block_id=user_block_id,
        recipient_instance_id=instance_id,
        recipient_handle=recipient_handle,
    )
    async with seat_admission(chat.live.seat_lock_for(topic_id, seat)):
        async with chat.session_factory() as session:
            place = await PlaceResolver(session).conversation(topic_id)
            # A room's addressed agent must still sit on its roster; a task's
            # agent is the task's own and sits on no roster.
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
            pending = seated is not None and await seat_has_unfinished_input(
                session, topic_id, seated[1]
            )
            # A member's own Claude Code runs only on its owner's machine: with
            # none of them online, the message waits for one to come back, and
            # the periodic scan of waiting messages starts it then.
            away = (
                not pending
                and instance_id is not None
                and await owner_is_away(session, instance_id, device_hub)
            )
            told_away = False
            if (pending or away) and user_block_id is not None:
                await defer_message(session, user_block_id)
                if away:
                    told_away = await _tell_once(session, user_block_id)
            await session.commit()
        if told_away:
            await chat.post_system_event(
                topic_id,
                say("ownerMachineAway"),
                meta=notice(EVENT_TURN_FAILED, severity=SEVERITY_INFO, who=WHO_HUMAN),
            )
        yield pending or away


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
