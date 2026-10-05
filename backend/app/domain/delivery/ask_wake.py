"""Record one committed Ask wake addressed only to its original seat."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.delivery.agent import instance_for_seat, record_agent
from app.domain.delivery.ledger import DeliveryEvent, event_id_for
from app.domain.delivery.models import Delivery
from app.domain.notification.models import NotificationType
from app.domain.topic_membership.services import TopicMemberService


async def expected_ask_session(sessions, delivery_id) -> str | None:
    """Read the pinned native session in a short owned session; never write.

    Internal admission caller owns recipient authorization. Returns only a string,
    keeping ledger rows inside delivery; no database access for ordinary prompts.
    """
    if delivery_id is None:
        return None
    async with sessions() as session:
        delivery = await session.get(Delivery, delivery_id)
        origin = delivery.payload.get("ask_origin") if delivery else None
        return origin["native_session_id"] if origin else None


@dataclass(frozen=True)
class SingleAnswerWake:
    event_id: uuid.UUID
    instance_id: uuid.UUID | None
    content: str
    meta: dict


async def single_answer_wake(
    session, *, project_id, room_id, block_id, asked_by, entry
) -> SingleAnswerWake:
    """Resolve a legacy single answer's destination and immutable wake values.
    Who may be woken is the room's roster, whichever conversation asked.

    Caller has authorized the answer and holds its question lock. Reads only;
    returned UUIDs/text/metadata contain no ORM rows and imply no delivery yet.
    """
    members = TopicMemberService(session)
    seat = (
        asked_by
        if asked_by in await members.agent_handles(room_id)
        else await members.addressable_agent_handle(room_id)
    )
    instance = (
        await instance_for_seat(session, project_id, seat) if seat is not None else None
    )
    event_id = event_id_for(NotificationType.MENTION, f"{block_id}:{entry['v']}")
    meta: dict = {"answer_to": str(block_id), "delivery_event_id": str(event_id)}
    if instance is not None:
        meta["agent_recipient"] = {
            "instance_id": str(instance.id),
            "handle": instance.handle,
            "mentioned": True,
        }
    kind = entry.get("kind")
    what = (
        entry["option"] or ""
        if kind == "option"
        else "以上都不是"
        if kind == "reject"
        else entry.get("note") or ""
    )
    suffix = (
        f"（{entry['note']}）"
        if kind in ("option", "reject") and entry.get("note")
        else ""
    )
    text = f"<@{seat}> {what}{suffix}" if seat else f"{what}{suffix}"
    return SingleAnswerWake(event_id, instance.id if instance else None, text, meta)


async def record_single_answer_wake(
    session, *, conversation_id, block_id, version, wake
):
    """Record intent after the timeline write, in the caller's authorized transaction.
    The wake goes to the conversation the question was asked in.

    Flush only, no commit or native I/O; absent legacy asker creates no delivery.
    """
    if wake.instance_id is not None:
        await record_agent(
            session,
            DeliveryEvent(
                id=wake.event_id,
                type=NotificationType.MENTION,
                payload={"answer_to": str(block_id), "v": version},
                occurred_at=datetime.now(UTC),
            ),
            conversation_id=conversation_id,
            instance_id=wake.instance_id,
            content=wake.content,
        )


async def record_ask_wake(
    session, *, project_id, room_id, conversation_id, origin, event_id, content, payload
):
    """Wake the agent that asked, in the conversation it asked in, if it still
    sits on the room's roster."""
    seat = origin["recipient_handle"]
    if seat not in await TopicMemberService(session).agent_handles(room_id):
        return None
    instance = await instance_for_seat(session, project_id, seat)
    if instance is None:
        return None
    await record_agent(
        session,
        DeliveryEvent(
            id=event_id,
            type=NotificationType.MENTION,
            payload={**payload, "ask_origin": origin},
            occurred_at=datetime.now(UTC),
        ),
        conversation_id=conversation_id,
        instance_id=instance.id,
        content=content,
    )
    return {
        "instance_id": str(instance.id),
        "handle": instance.handle,
        "mentioned": True,
    }
