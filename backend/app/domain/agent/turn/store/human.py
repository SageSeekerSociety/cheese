"""Durable human deliveries: claims, blocks and same-transaction SQL effects."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.output_effects import append_output
from app.domain.block.queries import client_delivery_bundle, message_reply_target
from app.domain.block.schemas import BlockOut
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.project.models import Project
from app.domain.room_task.place import Place, PlaceResolver

logger = logging.getLogger(__name__)


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


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


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
) -> StoredHuman:
    async with sessions() as session:
        # A room's own line, or a task's conversation in it.
        place = await PlaceResolver(session).conversation(topic_id)
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
        if delivery_key:
            assert client_id is not None
            legacy_blocks = await client_delivery_bundle(
                session, place.room_id, author=author, client_id=client_id
            )
            if legacy_blocks:
                anchor = next(
                    block
                    for block in legacy_blocks
                    if (block.meta or {}).get("client_id") == client_id
                )
                payloads = [
                    BlockOut.model_validate(block).model_dump(mode="json")
                    for block in legacy_blocks
                ]
                block_ids = [block.id for block in legacy_blocks]
                await idem.record_result(
                    session,
                    delivery_key,
                    {
                        "payloads": payloads,
                        "anchor_block_id": str(anchor.id),
                        "block_ids": [str(block_id) for block_id in block_ids],
                    },
                )
                await session.commit()
                return StoredHuman(place, [], payloads, anchor.id, block_ids, True)
        created_blocks: list[Block] = []
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
        reply_uuid = _parse_uuid(reply_to)
        reply_author: str | None = None
        if reply_uuid is not None:
            parent = await message_reply_target(session, reply_uuid)
            if parent is None or parent.conversation_id != place.conversation_id:
                logger.warning(
                    "dropped cross-topic reply_to (topic=%s, reply_to=%s)",
                    topic_id,
                    reply_to,
                )
                reply_uuid = None
            else:
                # 回谁的话就是对着谁说的：被回复的那条的作者定这一句的收件人
                # （#3210），由 `refuse` 认。
                reply_author = parent.author
        if content:
            content = prepared.content
            admission = await refuse(
                session, place, prepared, recipient, author, reply_author
            )
            user_block = await append_output(
                session,
                project_id=topic.project_id,
                conversation_id=place.conversation_id,
                author=author,
                author_type=AuthorType.participant,
                content=content,
                kind=BlockKind.message,
                turn_id=turn_id,
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
                },
            )
            if attribution_id is None:
                attribution_id = user_block.id
                user_block.turn_id = attribution_id
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
        payloads = [
            BlockOut.model_validate(block).model_dump(mode="json")
            for block in created_blocks
        ]
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
