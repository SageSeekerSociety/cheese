"""Resolve message-edit text through the actual human and agent send paths."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent.mentions import (
    _expand_mention_names,
    person_mentions,
    project_refs_text,
)
from app.domain.agent.turn.intake.rooms import _is_dm, room_roster
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block
from app.domain.project.reads import load_project
from app.domain.room_task.place import PlaceResolver
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService


@dataclass(frozen=True, slots=True)
class SentText:
    """A message's text as sending it would have stored it, and how it was sent."""

    room: Topic
    text: str
    # The names its mentions were read against; None where that way of sending
    # announces no mentions at all (a card's conversation).
    roster: list[dict] | None
    by_agent: bool


async def text_as_sent(
    session: AsyncSession, block: Block, author: str, content: str
) -> SentText:
    """What sending ``content`` as ``author`` where ``block`` is would have
    stored. An edit stores exactly that, by calling the same code.

    On a card it is `say_on_task`'s rewrite, for anyone. In the room the author
    is an agent when it holds one of the room's agent seats, the question the
    publication routes ask; its text goes through both halves of the
    publication rewrite, with the arguments a publication passes (no roster, no
    topic list). A person's goes through `person_mentions`, as
    `post_user_message` does."""
    place = await PlaceResolver(session).conversation(block.conversation_id)
    if place is None:
        raise NotFoundError(say("topicNotFound"))
    topic = place.room
    by_agent = await TopicMemberService(session).holds_an_agent_seat(topic, author)
    if place.task is not None:
        text = await project_refs_text(session, topic.project_id, topic.id, content)
        return SentText(topic, text, None, by_agent)
    if by_agent:
        text = await project_refs_text(session, topic.project_id, topic.id, content)
        roster = await room_roster(session, topic.project_id, topic)
        return SentText(topic, _expand_mention_names(text, roster, []), roster, True)
    project = await load_project(session, topic.project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    agent = await AgentInstanceService(session).for_topic(topic, project)
    mentions = await person_mentions(session, topic, content, agent, dm=_is_dm(topic))
    return SentText(topic, mentions.content, mentions.roster, False)
