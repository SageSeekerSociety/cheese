"""Interpret human messages in the writer's transaction, then publish effects."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent import own_calls
from app.domain.agent.announce import answer_questions, instance_of_seat
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
    session: AsyncSession,
    place: Place,
    prepared: HumanPreparation,
    recipient: dict,
    author: str,
    reply_author: str | None,
) -> CallAdmission:
    """这条消息该谁接，以及它算不算点了谁的名；都落在 `recipient` 上（原地改）。

    `recipient` 默认是这间房指的那位，两种情况改指到别人身上：

    - **@ 了人**：被 @ 的那一席接这一轮。只把 ``mentioned`` 置真不够 ——
      ``handle``/``instance_id`` 还指着房间默认的那位，@ 第二个队友仍旧是第一位接。
    - **回复了某条消息**：回谁的消息就是对着谁说的。任务房间里每条消息都默认点名
      任务那位（`prepare_human` 里 ``place.task is not None`` 那一支），于是「回另
      一位队友的提问」「点它卡上的选项」——两条路都只是一句话——仍然落到任务那位
      身上，提问的那位收不到答案（#3210）。人发的消息不在这里：它的作者是个人，
      不是席位。

    两条都成立时以 @ 为准。返回的席位是给 `record_human_effects` 的 ``skip`` 用
    的：那位的投递不重复记。无名可指的消息（只有附件、没有正文）到不了这里。
    """
    topic = place.room
    seat = next(
        (h for h in prepared.agent_handles if f"<@{h}>" in prepared.content), None
    )
    if seat is None and reply_author is not None:
        # 名册是现问的：`person_mentions` 只在正文里有 `@` 时才填这两个名单，
        # 而这条路一句 `@` 都没有。
        if reply_author in await TopicMemberService(session).agent_handles(topic.id):
            seat = reply_author
    if seat is not None:
        named = prepared.named_seats.get(seat)
        if named is not None:
            recipient["instance_id"], recipient["handle"] = named
        else:
            # A seat still under the room-derived handle names no instance, and
            # that seat IS the agent the room points at: the recipient already
            # names it.
            instance = await instance_of_seat(session, topic.project_id, seat)
            if instance is not None:
                recipient["instance_id"] = str(instance.id)
                recipient["handle"] = instance.handle
        recipient["mentioned"] = True
    refused = await own_calls.refused(session, prepared.project, recipient, author)
    return CallAdmission(seat, refused)


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
