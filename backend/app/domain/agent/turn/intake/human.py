"""Interpret human messages in the writer's transaction, then publish effects."""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent import own_calls
from app.domain.agent.announce import answer_questions, instance_of_seat
from app.domain.agent.ask import publish_answered
from app.domain.agent.mentions import (
    MENTION_ALL,
    MENTION_HERE,
    announce_mentions,
    person_mentions,
)
from app.domain.agent.queries import _agent_at
from app.domain.agent.turn.intake.rooms import _is_dm
from app.domain.agent.turn.store.human import (
    CallAdmission,
    HumanHandoff,
    HumanPreparation,
    persist_human_message,
    reply_parent,
)
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block
from app.domain.delivery.mention import mentioned_handles, record_mentions
from app.domain.identity.handles import (
    looks_like_agent_handle,
    names_a_person,
    recipient_seat,
)
from app.domain.project.reads import load_project
from app.domain.room_task import naming
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.place import Place
from app.domain.room_task.services import TaskService
from app.domain.thread.services import answered_in, thread_teammate
from app.domain.topic_membership.services import TopicMemberService


async def _owner_chats_named(session: AsyncSession, place: Place, content: str):
    """The private chats a message seating the personal agents it names asks
    their owners in (`own_calls.seat_if_named`)."""
    return await own_calls.owner_chats_to_ask(
        session,
        place.room,
        await own_calls.named_own_seats(session, place.room, content),
    )


async def prepare_human(
    session: AsyncSession,
    place: Place,
    content: str,
    author: str,
    *,
    handoff: HumanHandoff | None = None,
) -> HumanPreparation:
    """Read a person's message in the writer's session. What ``handoff`` carries
    the request already read, and is used instead of reading it again."""
    topic = place.room
    handoff = handoff or HumanHandoff()
    # Merged without a load, the request's row is this session's identity-map
    # entry: every later `get` of the project here (`_agent_at`) is answered by it.
    project = (
        await session.merge(handoff.project, load=False)
        if handoff.project is not None
        else await load_project(session, topic.project_id)
    )
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    await own_calls.seat_if_named(session, project, topic, content, author)
    return await _read_human(session, place, project, content, author, handoff.roster)


async def _read_human(
    session: AsyncSession,
    place: Place,
    project,
    content: str,
    author: str,
    roster,
    seating: Sequence[str] = (),
) -> HumanPreparation:
    """`prepare_human` after it seated whoever the message names: reads only, so
    the composer's preview (`preview_addressing`) reads a draft through it."""
    topic = place.room
    agent = await _agent_at(session, place)
    mentions = await person_mentions(
        session,
        topic,
        content,
        agent,
        dm=_is_dm(topic),
        members=roster,
        seating=seating,
    )
    # A two-seat private room addresses its peer only if that peer is an agent.
    seats = (
        await TopicMemberService(session).private_seats(topic.id)
        if _is_dm(topic)
        else None
    )
    if place.thread is not None:
        agent = await _thread_agent(session, place, project) or agent
    recipient = {
        "instance_id": str(agent.instance_id),
        "handle": agent.handle,
        "mentioned": (
            place.task is not None
            and place.task.status == TaskStatus.open
            and TaskService.takes_part(place.task, author)
            and not _names_only_people(mentions.content)
        )
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


@dataclass(frozen=True, slots=True)
class Addressing:
    """Who a person's message would be for, read the way sending reads it.

    ``agent`` is the AI teammate it starts a turn for (its seat, its name as
    stored, and where that name came from), or None when it starts none.
    ``name_source`` is the teammate's, the same field the roster carries: a
    teammate still wearing the name it was born with is shown in each reader's
    language (`teammateName`), so the composer needs it to do the same. The
    preview does not translate the name itself — it answers what the backend
    knows, and the screen says it. ``reason`` says which rule called it:
    ``mention`` (it @-s the teammate), ``reply`` (it replies to the teammate's
    message), ``task`` (its owner or a collaborator speaks in an open task),
    ``private`` (a private chat with the teammate). ``people`` are the persons it
    @-s. ``place`` is ``channel``, ``task`` or ``private``; ``task_status`` the
    task's status when it is said in one."""

    agent: dict | None
    reason: str | None
    people: list[str]
    place: str
    task_status: str | None

    def as_json(self) -> dict:
        return {
            "agent": self.agent,
            "reason": self.reason,
            "people": self.people,
            "place": self.place,
            "task_status": self.task_status,
        }


async def preview_addressing(
    session: AsyncSession,
    place: Place,
    content: str,
    author: str,
    *,
    reply_to: uuid.UUID | None,
    with_text: bool,
    may_summon: bool,
) -> Addressing:
    """Who ``author``'s message would be for if sent now, without sending it.

    The same reads sending makes (`prepare_human`, `refuse_call`), on the
    same rules, minus every write: a personal agent the draft names is read as
    seated rather than seated, and nothing is stored, delivered or woken.
    ``with_text`` is False for a send of attachments alone, which no @ or reply
    calls anyone by (`persist_human_message` reads ``refuse`` only for text).
    ``may_summon`` is the sender's right to use the project's AI teammates
    (#3830): without it the message wakes nobody, whoever it names."""
    project = await load_project(session, place.project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    seating = await own_calls.named_own_seats(session, place.room, content)
    prepared = await _read_human(
        session, place, project, content, author, None, seating
    )
    recipient = prepared.recipient
    reason = None
    if recipient["mentioned"]:
        reason = "task" if place.task is not None else "private"
    if with_text:
        parent = await reply_parent(session, place, reply_to)
        admission = await refuse_call(
            session,
            place,
            prepared,
            recipient,
            author,
            parent.author if parent is not None else None,
        )
        if admission.addressed is not None:
            reason = (
                "mention"
                if f"<@{admission.addressed}>" in prepared.content
                else "reply"
            )
    agent = None
    if recipient["mentioned"] and may_summon:
        instance = await AgentInstanceService(session).get_in_project(
            project_id=project.id, instance_id=uuid.UUID(recipient["instance_id"])
        )
        agent = {
            "handle": recipient_seat(recipient),
            "name": instance.display_name,
            "name_source": str(instance.name_source),
        }
    people = [
        h
        for h in dict.fromkeys(mentioned_handles(prepared.content))
        if h != author and (h in (MENTION_ALL, MENTION_HERE) or names_a_person(h))
    ]
    return Addressing(
        agent=agent,
        reason=reason if agent is not None else None,
        people=people,
        place=(
            "task"
            if place.task is not None
            else "private"
            if _is_dm(place.room)
            else "channel"
        ),
        task_status=place.task.status.value if place.task is not None else None,
    )


def _names_only_people(content: str) -> bool:
    """Whether ``content`` @-s someone and every one it @-s is a person.

    A task calls its teammate with every message its owner or a collaborator
    says, except one that says whom it is for and names no AI teammate there:
    an @ of a person is said to that person, and the task's teammate does not
    pick it up. Anyone else in the project may speak in a task as in a 支线:
    their message calls the teammate only when it @-s it, and otherwise rides
    into its next turn as something said there. ``@all``/``@here``
    name the whole channel, the teammate included."""
    handles = mentioned_handles(content)
    return bool(handles) and all(
        h not in (MENTION_ALL, MENTION_HERE) and names_a_person(h) for h in handles
    )


async def _thread_agent(session: AsyncSession, place: Place, project):
    """Who a 支线's message is for when it calls nobody: the teammate the 支线
    is talking with (`thread_teammate`). Handed to the channel's default
    instead, it reached no one: the default has no turn there to hear it, and
    the next turn of the teammate that does leaves out a message addressed to
    somebody else."""
    assert place.thread is not None
    seat = await thread_teammate(session, place.thread)
    if seat is None:
        return None
    return await AgentInstanceService(session).for_seat_handle(project, seat)


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
    refused = await own_calls.refused(session, prepared.project, recipient)
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
        handoff: HumanHandoff | None = None,
        by_assistant: bool = False,
        expects_agent: bool = False,
    ) -> tuple[list[dict], uuid.UUID, list[uuid.UUID], bool]:
        stored = await persist_human_message(
            self.sessions,
            prepare=partial(prepare_human, handoff=handoff),
            said_in=_owner_chats_named,
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
            by_assistant=by_assistant,
            handoff=handoff,
            expects_agent=expects_agent,
        )
        if not stored.duplicate:
            await publish_answered(stored.place.conversation_id, stored.answered)
            if names_a_person(author) and stored.place.task is not None:
                naming.nudge(stored.place.task.id, "message")
            if stored.place.thread is not None:
                await self._thread_replied(stored.place.conversation_id)
        return stored.payloads, stored.anchor_id, stored.block_ids, stored.duplicate
