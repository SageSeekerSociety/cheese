"""Interpret human messages in the writer's transaction, then publish effects."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent import own_calls
from app.domain.agent.announce import answer_questions
from app.domain.agent.ask import publish_answered
from app.domain.agent.mentions import announce_mentions, person_mentions
from app.domain.agent.queries import _agent_at
from app.domain.agent.turn.intake.rooms import _is_dm
from app.domain.agent.turn.store.human import (
    CallAdmission,
    HumanPreparation,
    persist_human_message,
)
from app.domain.block.models import Block
from app.domain.delivery.mention import record_mentions
from app.domain.identity.handles import looks_like_agent_handle, names_a_person
from app.domain.project.reads import load_project
from app.domain.room_task import naming
from app.domain.room_task.place import Place
from app.domain.thread.services import answered_in
from app.domain.topic_membership.services import TopicMemberService


async def prepare_human(
    session: AsyncSession, place: Place, content: str, author: str
) -> HumanPreparation:
    topic = place.room
    project = await load_project(session, topic.project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    await own_calls.seat_if_named(session, project, topic, content, author)
    agent = await _agent_at(session, place)
    mentions = await person_mentions(session, topic, content, agent, dm=_is_dm(topic))
    # A two-seat private room addresses its peer only if that peer is an agent.
    seats = (
        await TopicMemberService(session).private_seats(topic.id)
        if _is_dm(topic)
        else None
    )
    recipient = {
        "instance_id": str(agent.instance_id),
        "handle": agent.handle,
        "mentioned": place.task is not None
        or (seats is not None and looks_like_agent_handle(seats[1])),
    }
    return HumanPreparation(
        project=project,
        content=mentions.content,
        roster=mentions.roster,
        agent_handles=mentions.agent_handles,
        named_seats={
            h: (str(seat.id), seat.handle) for h, seat in mentions.by_seat.items()
        },
        recipient=recipient,
    )


async def refuse_call(
    session: AsyncSession, prepared: HumanPreparation, recipient: dict, author: str
) -> CallAdmission:
    # Select which named seat answers, before checking whose own agent it is.
    addressed = next(
        (h for h in prepared.agent_handles if f"<@{h}>" in prepared.content), None
    )
    if addressed is not None:
        recipient["mentioned"] = True
        named = prepared.named_seats.get(addressed)
        if named is not None:
            recipient["instance_id"], recipient["handle"] = named
    refused = await own_calls.refused(session, prepared.project, recipient, author)
    return CallAdmission(addressed, refused)


async def record_human_effects(
    session: AsyncSession,
    place: Place,
    user_block: Block,
    prepared: HumanPreparation,
    recipient: dict,
    author: str,
    refused: str | None,
    addressed: str | None,
    quoted_context: dict | None,
) -> list[Block]:
    await own_calls.say_refused(session, place, user_block, refused)
    await announce_mentions(session, place.room, user_block, author, prepared.roster)
    answered = await answer_questions(session, user_block, recipient)
    if len(prepared.agent_handles) > 1:
        await record_mentions(
            session,
            project_id=place.project_id,
            room_id=place.room_id,
            conversation_id=await answered_in(session, user_block),
            block_id=user_block.id,
            author=author,
            content=user_block.content,
            quoted_context=quoted_context,
            by_agent=False,
            occurred_at=datetime.now(UTC),
            skip=frozenset({addressed} if addressed else ()),
        )
    return answered


class HumanMessages:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        thread_replied: Callable[[uuid.UUID], Awaitable[None]],
    ) -> None:
        self.sessions = sessions
        self._thread_replied = thread_replied

    async def post_user_message(
        self,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        turn_id: uuid.UUID | None,
        reply_to: str | None,
        attachments: list[dict] | None = None,
        client_id: str | None = None,
        quoted_context: dict | None = None,
    ) -> tuple[list[dict], uuid.UUID, list[uuid.UUID], bool]:
        stored = await persist_human_message(
            self.sessions,
            prepare=prepare_human,
            refuse=refuse_call,
            record_effects=record_human_effects,
            topic_id=topic_id,
            author=author,
            content=content,
            turn_id=turn_id,
            reply_to=reply_to,
            attachments=attachments,
            client_id=client_id,
            quoted_context=quoted_context,
        )
        if not stored.duplicate:
            await publish_answered(stored.place.conversation_id, stored.answered)
            if names_a_person(author) and stored.place.task is not None:
                naming.nudge(stored.place.task.id, "message")
            await self._thread_replied(stored.place.conversation_id)
        return stored.payloads, stored.anchor_id, stored.block_ids, stored.duplicate
