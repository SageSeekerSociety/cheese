"""Who may set a member's own Claude Code to work, at the moment a message does.

A member's own agent (#2991) is called by its owner alone. It follows its owner
into the project's rooms: the first message in which its owner names it seats it
in that room, as any teammate sits there. Anyone else naming it reaches nobody,
and is told why beside their message.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_instance.own import may_call, owner_of
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import agent_instance_handle
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.services import user_by_handle


async def seat_if_named(
    session: AsyncSession, topic, content: str, author: str
) -> None:
    """Seat the author's own agent in this room when the message names it."""
    if not content or "<@" not in content:
        return
    person = await user_by_handle(session, author)
    if person is None:
        return
    own = await session.scalar(
        select(AgentInstance.id)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(
            AgentInstance.project_id == topic.project_id,
            OwnAgent.owner_user_id == person.id,
        )
    )
    if own is None:
        return
    seat = agent_instance_handle(own)
    if f"<@{seat}>" in content:
        await TopicMemberService(session).ensure_agent_seat(topic.id, seat)


async def refused(session: AsyncSession, project, recipient: dict, author: str):
    """When this message calls an agent its author may not call, take the call
    back and name whose agent it is; None when the call stands."""
    if not recipient.get("mentioned") or not recipient.get("instance_id"):
        return None
    instance_id = uuid.UUID(recipient["instance_id"])
    if await may_call(session, instance_id, author, project.settings):
        return None
    recipient["mentioned"] = False
    owner = await owner_of(session, instance_id)
    return owner[1] if owner is not None else ""


async def say_refused(session: AsyncSession, block, owner_name: str) -> None:
    """Beside the message, the reason it reached nobody."""
    await BlockRepository(session).add(
        project_id=block.project_id,
        conversation_id=block.conversation_id,
        author=block.author,
        author_type=AuthorType.participant,
        content=say("ownAgentOwnerOnly", owner=owner_name),
        kind=BlockKind.event,
        turn_id=block.turn_id,
        meta={"in_room": False},
    )
