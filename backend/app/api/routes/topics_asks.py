"""`POST /topics/{topic_id}/asks`: an agent's question, posted into its conversation.

There is no answer route. An answer is an ordinary message — a click on one of
the question's options sends the option's text as a reply to it — and it goes
through `POST /topics/{topic_id}/messages` like anything else a person says.
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker
from app.api.response import ok
from app.api.routes.topics import BlockOut, DbSession
from app.core.errors import ForbiddenError
from app.core.sentences import say
from app.domain.agent.ask import ask
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/{topic_id}/asks")
async def create_questions(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
):
    """Post 1-3 questions with quick replies under the calling agent's seat.

    Returns at once; nothing waits for the answer. The caller ends its turn, and
    the reply someone sends starts the next one.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        project_id=place.project_id, topic_id=place.conversation_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    if actor.via != "cheese" or not await TopicMemberService(db).holds_an_agent_seat(
        place.room, actor.handle
    ):
        raise ForbiddenError(say("askAgentSeatOnly"))
    rows = await ask(db, place=place, seat=actor.handle, body=body)
    blocks = [BlockOut.model_validate(row).model_dump(mode="json") for row in rows]
    await db.commit()
    for block in blocks:
        await get_broker().publish(
            str(place.conversation_id), {"type": "assistant_block", "block": block}
        )
    return ok({"blocks": blocks, "request_id": body.get("request_id")})
