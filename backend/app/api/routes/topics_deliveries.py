"""定时投递（结论 17）：「到点把这条递给我」。

A slice of `app/api/routes/topics.py`, which is over its 1,500-line cap and may
only shrink. It moved when the route stopped being agent-only: a person in the
room sets a reminder for themselves through it, the same way an AI teammate
calls `cheese_deliver_at`.

Ordering. This module sorts after `topics.py` and `topics_compute.py` and before
`topics_documents.py` (`compute` < `deliveries` < `documents`). No route
registered before it has a parameter where `deliveries` sits, so the path still
reaches this handler. The module mounts itself through
`app.main._discover_routers`.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.topics import DbSession
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.delivery.timer import deliver_at
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/{topic_id}/deliveries")
async def ask_for_a_delivery(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """请平台在某个时刻把这条递给请求者自己。

    房间里的人和 AI 队友走同一条路。能不能设，看的是这个房间收不收这个调用者，
    不看它是不是 agent，所以这里自己把门（`enforce=True`）：没有凭据的调用者进
    不来，房间外的人 403。

    收件人不在正文里，因为这条原语只有一个收件人规则——**就是请求它的那个参与者**。
    给别人设闹钟是另一件事：往别人的收件箱里放一条对方没要过的提醒，没有人要过它。
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    recipient = actor.handle
    if not actor.authenticated:
        # Only the global sandbox token gets here (`authorize_topic` refuses every
        # other unauthenticated caller). It names nobody, so the request is the
        # room's agent's.
        recipient = await TopicMemberService(db).resolve_agent_handle(
            topic_id, room_id=place.room_id
        )
    raw = (body.get("at") or "").strip()
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(say("atMustBeIso")) from None
    row = await deliver_at(
        db,
        when=when,
        event=body.get("content") or "",
        recipient=recipient,
        conversation_id=place.conversation_id,
        project_id=place.project_id,
    )
    return ok({"id": str(row.id), "at": when.isoformat(), "to": recipient})
