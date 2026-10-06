"""Admit a turn's first input under its seat's lock, after project admission."""

from contextlib import asynccontextmanager

from app.domain.agent.seat_admission import seat_admission
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.models import Delivery


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
    async with seat_admission(chat._seat_lock_for(topic_id, seat)):
        from app.domain.agent.queries import conversation_seat
        from app.domain.room_task.place import PlaceResolver

        async with chat.session_factory() as session:
            place = await PlaceResolver(session).conversation(topic_id)
            # A room's addressed agent must still sit on its roster; a task's
            # agent is the task's own and sits on no roster.
            if instance_id is not None and place is not None and place.task is None:
                from app.core.errors import ValidationError
                from app.domain.identity.handles import agent_instance_handle
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
            if pending and user_block_id is not None:
                from app.domain.agent.pending_messages import defer_message

                await defer_message(session, user_block_id)
            await session.commit()
        yield pending
