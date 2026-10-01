"""Record one committed Ask wake addressed only to its original seat."""

from datetime import UTC, datetime

from app.domain.delivery.agent import instance_for_seat, record_agent
from app.domain.delivery.ledger import DeliveryEvent
from app.domain.notification.models import NotificationType
from app.domain.topic_membership.services import TopicMemberService


async def record_ask_wake(
    session, *, project_id, topic_id, origin, event_id, content, payload
):
    seat = origin["recipient_handle"]
    if seat not in await TopicMemberService(session).agent_handles(topic_id):
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
        topic_id=topic_id,
        instance_id=instance.id,
        content=content,
    )
    return {
        "instance_id": str(instance.id),
        "handle": instance.handle,
        "mentioned": True,
    }
