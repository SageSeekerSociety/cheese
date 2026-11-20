"""`POST /topics/{topic_id}/asks`: an agent's question, posted into its conversation.

There is no answer route. An answer is an ordinary message — a click on one of
the question's options sends the option's text as a reply to it — and it goes
through `POST /topics/{topic_id}/messages` like anything else a person says.
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ForbiddenError
from app.core.sentences import say
from app.domain.agent.ask import ask
from app.domain.block.adapters import dump_blocks_json
from app.domain.room_task.place import place_or_404
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/{topic_id}/asks", dependencies=[ROUTE_DECIDES])
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
    place = await place_or_404(db, topic_id)
    actor = await resolver.resolve(
        project_id=place.project_id, topic_id=place.conversation_id
    )
    await resolver.authorize_topic(
        actor,
        project_id=place.project_id,
        topic_id=place.room_id,
        room=place.room,
        enforce=True,
    )
    if actor.via != "cheese" or not await TopicMemberService(db).holds_an_agent_seat(
        place.room, actor.handle
    ):
        raise ForbiddenError(say("askAgentSeatOnly"))
    rows = await ask(db, place=place, seat=actor.handle, body=body)
    blocks = await dump_blocks_json(rows)
    await db.commit()
    return ok({"blocks": blocks, "request_id": body.get("request_id")})
