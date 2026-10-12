"""Continue failed work, or hand over inputs the conversation still owes."""

import uuid

from app.api.deps import get_broker
from app.api.response import ok
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.domain.agent.retry import take_failed_reply
from app.domain.agent.runtime import addressed_to_agent
from app.domain.delivery.agent import redispatch_undelivered
from app.domain.topic_membership.services import TopicMemberService


async def summon_in_place(*, place, actor, body, db, chat, runner):
    if chat.has_running_turn(place.conversation_id):
        return ok({"started": False, "reason": "working"})
    if place.room.status == "archived" or (
        place.task is not None and place.task.closed_at is not None
    ):
        return ok({"started": False, "reason": "nothing_pending"})
    block_id = body.get("retry_block_id")
    if block_id is not None:
        try:
            block_id = uuid.UUID(str(block_id))
        except ValueError as exc:
            raise UnprocessableEntityError(say("invalidRequestParameters")) from exc
    members = TopicMemberService(db)
    seats = await members.agent_handles(place.room_id)
    candidate, changed = await take_failed_reply(
        db, place.conversation_id, block_id=block_id, seats=seats
    )
    if changed is not None:
        await db.commit()
        await get_broker().publish(
            str(place.conversation_id), {"type": "block_updated", "block": changed}
        )
    elif block_id is not None:
        return ok({"started": False, "reason": "nothing_pending"})
    if candidate is not None:
        runner.submit(
            chat,
            place.conversation_id,
            author="system",
            content=candidate.content,
            addressed=addressed_to_agent(candidate.seat),
            reply_to=candidate.reply_to,
            is_resume=True,
            continuation_id=candidate.continuation_id,
        )
        return ok({"started": True})
    if block_id is not None:
        # An explicit card must not turn into unrelated unread-message work.
        return ok({"started": False, "reason": "nothing_pending"})
    if not await chat.has_unread_input(place.conversation_id):
        # Only demonstrably never-sent deliveries may be redispatched here.
        if await redispatch_undelivered(
            chat.session_factory,
            conversation_id=place.conversation_id,
            chat=chat,
            runner=runner,
        ):
            return ok({"started": True})
        return ok({"started": False, "reason": "nothing_pending"})
    # Unread inputs retain their original addressee; a forgotten mention falls
    # back to the room's default seat. No participant message is reposted.
    seat = await chat.pending_seat(place.conversation_id)
    if seat is None or seat not in seats:
        seat = await members.addressable_agent_handle(place.room_id)
    runner.submit(
        chat,
        place.conversation_id,
        author="system",
        content="有人请你看一下这里还没读到的消息，照常处理。",
        addressed=addressed_to_agent(seat),
        nudge_event=(
            say("pendingHandedTo", actor=f"<@{actor.handle}>", seat=f"<@{seat}>")
            if seat
            else say("pendingHandedOver", actor=f"<@{actor.handle}>")
        ),
    )
    return ok({"started": True})
