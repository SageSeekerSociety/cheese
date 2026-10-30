"""Durable human deliveries: claims, blocks and same-transaction SQL effects."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ConflictError, NotFoundError
from app.core.sentences import say
from app.core.work_context import parse_work_id
from app.domain.block.adapters import dump_blocks_json
from app.domain.block.models import BY_ASSISTANT_META_KEY, Block, BlockKind
from app.domain.block.output_effects import append_output
from app.domain.block.queries import message_reply_target
from app.domain.block.repositories import hold_numbering
from app.domain.block.schemas import reply_preview
from app.domain.event_author import AuthorType
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.membership.roster import Member
from app.domain.project.models import Project
from app.domain.room_task.place import Place, PlaceResolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HumanHandoff:
    """What the request already read before the writer opens its own session.

    The writer's session is its own on purpose; these rows are what it would
    otherwise read again. Each field is optional: ``None`` means the request
    did not read it, and the writer reads it as before. ``roster`` is the
    :func:`~app.domain.membership.roster.roster` read of the room's project.
    """

    project: Project | None = None
    roster: tuple[Member, ...] | None = None
    place: Place | None = None


async def _merge_place(session: AsyncSession, place: Place) -> Place:
    room = await session.merge(place.room, load=False)
    task = (
        await session.merge(place.task, load=False) if place.task is not None else None
    )
    thread = (
        await session.merge(place.thread, load=False)
        if place.thread is not None
        else None
    )
    return Place(room=room, task=task, thread=thread)


@dataclass(frozen=True, slots=True)
class HumanPreparation:
    project: Project
    content: str
    roster: list[dict]
    agent_handles: list[str]
    named_seats: dict[str, tuple[str, str]]
    recipient: dict


@dataclass(frozen=True, slots=True)
class CallAdmission:
    addressed: str | None
    refused: str | None


@dataclass(frozen=True, slots=True)
class StoredHuman:
    place: Place
    answered: list[Block]
    payloads: list[dict]
    anchor_id: uuid.UUID
    block_ids: list[uuid.UUID]
    duplicate: bool


class HumanEffects(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        place: Place,
        user_block: Block,
        prepared: HumanPreparation,
        recipient: dict,
        author: str,
        refused: str | None,
        addressed: str | None,
        quoted_context: dict | None,
        /,
    ) -> list[Block]: ...


class AddressingChanged(ConflictError):
    """A message sent as one for an AI teammate that, read now, is for none.

    Raised before anything is written. The route answers it with who the
    message is for now (`preview_addressing`), so the sender chooses again."""

    def __init__(self, data: dict | None = None) -> None:
        super().__init__(say("addressingChanged"), data)


async def reply_parent(
    session: AsyncSession, place: Place, reply_to: uuid.UUID | None
) -> Block | None:
    """The message ``reply_to`` names, when it is one in ``place``'s
    conversation. A reply to anything else threads under nothing (B3)."""
    if reply_to is None:
        return None
    parent = await message_reply_target(session, reply_to)
    if parent is None or parent.conversation_id != place.conversation_id:
        return None
    return parent


async def persist_human_message(
    sessions: async_sessionmaker[AsyncSession],
    *,
    prepare: Callable[[AsyncSession, Place, str, str], Awaitable[HumanPreparation]],
    refuse: Callable[
        [AsyncSession, Place, HumanPreparation, dict, str, str | None],
        Awaitable[CallAdmission],
    ],
    record_effects: HumanEffects,
    topic_id: uuid.UUID,
    author: str,
    content: str,
    turn_id: uuid.UUID | None,
    reply_to: str | None,
    attachments: list[dict] | None = None,
    client_id: str | None = None,
    quoted_context: dict | None = None,
    by_assistant: bool = False,
    handoff: HumanHandoff | None = None,
    expects_agent: bool = False,
) -> StoredHuman:
    async with sessions() as session:
        # A room's own line, or a task's conversation in it.
        place = (
            await _merge_place(session, handoff.place)
            if handoff is not None
            and handoff.place is not None
            and handoff.place.conversation_id == topic_id
            else await PlaceResolver(session).conversation(topic_id)
        )
        if place is None:
            raise NotFoundError(say("topicNotFound"))
        topic = place.room
        delivery_key = (
            action_key(place.conversation_id, "chat_message", author, client_id)
            if client_id
            else None
        )
        if delivery_key and not await idem.claim(
            session,
            delivery_key,
            action="chat_message",
            scope_id=str(place.conversation_id),
        ):
            stored = await idem.stored_result(session, delivery_key)
            if stored is None:
                # i18n-exempt: corrupt delivery invariant; logged, not a user refusal
                raise RuntimeError("committed chat delivery has no stored result")
            return StoredHuman(
                place,
                [],
                list(stored["payloads"]),
                uuid.UUID(stored["anchor_block_id"]),
                [uuid.UUID(value) for value in stored["block_ids"]],
                True,
            )
        # Who the message is for turns on its task as it is when the message
        # is numbered: open or closed, its owner, collaborators and teammate
        # (`prepare`). Whatever changes those also says so in this
        # conversation, in the same transaction (`room_task.heard`, the close
        # and reopen lines, archive's), so it holds this conversation's
        # numbering until it commits. Taken first, before the task is read,
        # that lock orders this message against every such change: one
        # committed before it is read here, one after it lands after this
        # message. The task row is read without a lock of its own: a lock on
        # it would be a second one, which the changes take in the other order.
        await hold_numbering(session, place.conversation_id)
        if place.task is not None:
            # The place may be the request's copy, read before this
            # transaction began.
            await session.refresh(place.task)
        created_blocks: list[Block] = []
        # Written by the author's own 芝士: every block of the send says so.
        written_for = {BY_ASSISTANT_META_KEY: True} if by_assistant else {}
        prepared = await prepare(session, place, content, author)
        recipient = prepared.recipient
        anchor_id: uuid.UUID | None = None
        answered: list[Block] = []
        attribution_id = turn_id
        # B3: a reply threads under a block IN THIS TOPIC. A client that
        # kept a stale reply target across a topic switch would otherwise
        # write a cross-topic edge into the conversation tree — invisible on
        # screen (the reader's timeline can't resolve the parent, so no
        # reply cue renders) and wrong in the data that 记忆/摘要 rebuild
        # from. Drop the edge, keep the message: losing the thread link is
        # recoverable, refusing the send is not.
        reply_uuid = parse_work_id(reply_to)
        parent = await reply_parent(session, place, reply_uuid)
        if reply_uuid is not None and parent is None:
            logger.warning(
                "dropped cross-topic reply_to (topic=%s, reply_to=%s)",
                topic_id,
                reply_to,
            )
            reply_uuid = None
        # 回谁的话就是对着谁说的：被回复的那条的作者定这一句的收件人（#3210），由
        # `refuse` 认。
        reply_author = parent.author if parent is not None else None
        admission = (
            await refuse(session, place, prepared, recipient, author, reply_author)
            if content
            else CallAdmission(None, None)
        )
        if expects_agent and not recipient["mentioned"]:
            # The sender was shown that this goes to an AI teammate, and it no
            # longer would (the task closed while they typed). Sent anyway it
            # would land as discussion they never chose; refused, nothing is
            # written and their draft stays theirs to send either way.
            raise AddressingChanged()
        if content:
            content = prepared.content
            # A participant's own message groups under itself when the send
            # carries no ambient turn: `turn_id` is what puts a send's blocks
            # together. Mint that id here so the self-reference is one INSERT
            # column — assigning it after the flush cost an UPDATE and a
            # whole-row read to resynchronise.
            self_grouping_id = uuid.uuid4() if attribution_id is None else None
            user_block = await append_output(
                session,
                project_id=topic.project_id,
                conversation_id=place.conversation_id,
                author=author,
                author_type=AuthorType.participant,
                content=content,
                kind=BlockKind.message,
                block_id=self_grouping_id,
                turn_id=self_grouping_id or turn_id,
                reply_to=reply_uuid,  # B3: thread under another
                # The sender's own id for this send, echoed straight back on
                # the broadcast. A client that showed the message the instant
                # it was typed (§14.1 实时) needs to recognise its own copy
                # coming home; matching on text cannot do that, because this
                # method rewrites the text on the way in.
                meta={
                    "agent_recipient": recipient,
                    **({"client_id": client_id} if client_id else {}),
                    **(
                        {"quoted_context": quoted_context}
                        if quoted_context is not None
                        else {}
                    ),
                    **written_for,
                },
            )
            if attribution_id is None:
                attribution_id = user_block.id
            answered = await record_effects(
                session,
                place,
                user_block,
                prepared,
                recipient,
                author,
                admission.refused,
                admission.addressed,
                quoted_context,
            )
            anchor_id = user_block.id
            created_blocks.append(user_block)
            # 收起来的支线里说了话就是放回来了（`thread/recycle.py`）：它当场回到默认
            # 那一栏，和人点「恢复」是同一条路。放回来的是「在哪一栏、算不算数」这件
            # 事，不是那次失败 —— 它已经过了时限，所以频道那一行的红灯不会跟着回来；
            # 那间房要再红，得是之后又有一次没人恢复的失败（`waits` 会往后看）。
            # 收起来的一栏本来就是只读的，所以每次写到这儿都是一次「回来」，不用再问
            # 是谁说的。
            thread = getattr(place, "thread", None)
            if thread is not None and thread.recycled_at is not None:
                thread.recycled_at = None
        # 图片输入: each image = an attachment block. content = the worktree
        # path (a REAL file, uploaded before this message), mime_type = how
        # to render it — structured fields, never parsed out of prose.
        for index, att in enumerate(attachments or []):
            att_block = await append_output(
                session,
                project_id=topic.project_id,
                conversation_id=place.conversation_id,
                author=author,
                author_type=AuthorType.participant,
                content=str(att.get("path") or ""),
                kind=BlockKind.attachment,
                mime_type=str(att.get("mime") or "") or None,
                turn_id=attribution_id,
                # An image-only send still honors the reply thread (B3).
                reply_to=None if content else reply_uuid,
                meta={
                    "agent_recipient": recipient,
                    **(
                        {"client_id": client_id}
                        if not content and index == 0 and client_id
                        else {}
                    ),
                    **written_for,
                },
            )
            if attribution_id is None:
                attribution_id = att_block.id
                att_block.turn_id = attribution_id
            if anchor_id is None:
                anchor_id = att_block.id
            created_blocks.append(att_block)
        if anchor_id is None:  # guarded by the route, but never crash a turn
            raise NotFoundError(say("chatMessageEmpty"))
        payloads = await dump_blocks_json(created_blocks)
        # The reply cue needs a line from the parent, and the reader's timeline
        # may not hold it. It rides with the reply; the parent stays out of the
        # page. Written before the idempotency record so a retry carries it too.
        if parent is not None:
            preview = reply_preview(parent)
            for block, payload in zip(created_blocks, payloads, strict=True):
                if block.reply_to is not None:
                    payload["reply_preview"] = preview
        block_ids = [block.id for block in created_blocks]
        if delivery_key:
            await idem.record_result(
                session,
                delivery_key,
                {
                    "payloads": payloads,
                    "anchor_block_id": str(anchor_id),
                    "block_ids": [str(block_id) for block_id in block_ids],
                },
            )
        await session.commit()
    return StoredHuman(place, answered, payloads, anchor_id, block_ids, False)
