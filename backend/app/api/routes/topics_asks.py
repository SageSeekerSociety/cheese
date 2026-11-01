"""`POST /topics/{topic_id}/asks`: an agent's question, posted into its conversation.
`POST /topics/{task_id}/accept-card/ask`: the same, about the decision a task's
waiting delivery needs, with the buttons that make it.

There is no answer route. An answer is an ordinary message — a click on one of
the question's options sends the option's text as a reply to it — and it goes
through `POST /topics/{topic_id}/messages` like anything else a person says.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES, Can, InChannel
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.ask import ask, ask_to_decide
from app.domain.block.adapters import dump_blocks_json
from app.domain.block.schemas import BlockOut
from app.domain.project_permissions import Permission
from app.domain.review.services import AcceptService
from app.domain.room_task.place import place_or_404
from app.domain.room_task.services import TaskService
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


class DecisionAsked(BaseModel):
    """The question `ask_for_the_decision` posted, as the room shows it."""

    blocks: list[BlockOut]


@router.post("/{topic_id}/accept-card/ask", **typed_response(DecisionAsked))
async def ask_for_the_decision(
    at: Annotated[InChannel, Can(Permission.speak, at="topic_id")],
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
):
    """The task's own teammate asks a person to decide its waiting delivery:
    its question, with the buttons (``offer``: ``accept``, ``close``) that
    accept the delivery or close the task when a person clicks one
    (`agent.ask.ask_to_decide`). ``topic_id`` is the task's."""
    place, actor, task = at.place, at.actor, at.place.task
    if task is None:
        raise NotFoundError(say("taskNotFound"))
    if (
        actor.via != "cheese"
        or resolver.credential_conversation() != task.id
        or not await TopicMemberService(db).holds_an_agent_seat(
            place.room, actor.handle
        )
    ):
        raise ForbiddenError(say("askAgentSeatOnly"))
    TaskService.require_open(task)
    card = await AcceptService(db).waiting_card(task.id)
    if card is None:
        raise ConflictError(say("askDecisionNothingWaits"))
    offer = body.get("offer")
    rows = await ask_to_decide(
        db,
        place=place,
        seat=actor.handle,
        card=card,
        question=body.get("question"),
        offer=offer if isinstance(offer, list) else [],
    )
    blocks = await dump_blocks_json(rows)
    await db.commit()
    return ok({"blocks": blocks})
