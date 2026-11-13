"""Assistant idempotency and durable writes, with same-transaction SQL effects."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ConflictError
from app.core.sentences import say
from app.domain.agent.turn.store.events import EventAuthorResolver
from app.domain.block.models import CHECKLIST_META_KEY, Block, BlockKind
from app.domain.block.output_effects import append_output
from app.domain.block.queries import (
    any_output_event_exists,
    last_turn_output,
    message_reply_target,
)
from app.domain.block.schemas import BlockOut, reply_preview
from app.domain.event_about import EventAbout, landing
from app.domain.event_author import AuthorType
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.identity.handles import agent_handle_column, looks_like_agent_handle
from app.domain.topic.models import Topic, is_private_chat


class TopicLoader(Protocol):
    async def __call__(
        self, session: AsyncSession, topic_id: uuid.UUID
    ) -> Topic | None: ...


class AssistantMentions(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        topic: Topic | None,
        text: str,
        roster: list[dict] | None,
        topic_refs: list[dict],
        /,
    ) -> tuple[str, list[dict]]: ...


class MentionNotifier(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        topic: Topic,
        block: Block,
        author: str,
        roster: list[dict],
        /,
        *,
        flag_unresolved: bool,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredAssistant:
    payload: dict | None
    written: bool
    author: str | None
    #: The attachment blocks written in the same transaction, in send order.
    attachments: tuple[dict, ...] = ()


async def stored_publication(
    sessions: async_sessionmaker[AsyncSession],
    *,
    topic_id: uuid.UUID,
    author: str,
    publication_id: str,
) -> dict | None:
    """The result a request with this id already stored, or None.

    `chat_send`'s retry path asks before it reads or writes any attachment: a
    retry of a message the room already has must not leave a second copy of
    the bytes behind. The decision stays here, under the same key and the same
    input rule `persist_assistant_message` applies.
    """
    key = action_key(topic_id, "chat-publish", author, publication_id)
    async with sessions() as session:
        return await idem.stored_result(session, key)


async def _question_talked_past(
    session: AsyncSession, conversation_id: uuid.UUID, turn_id: uuid.UUID
) -> uuid.UUID | None:
    """The message a turn in a channel's main line answers, when another person
    spoke between it and this answer: the answer then quotes it, so a reader
    sees which line it answers. A turn a message started is named after that
    message (``turn_id``).

    None when the turn was not started by a person's message here, when nobody
    but its asker spoke after it, or when an AI teammate already said something
    after it: the teammate's first message of the turn is the answer, and a
    second one follows its own. Who spoke is told by the handle's kind
    (`agent_handle_column`), not compared with the author of this message: a
    teammate is written under its seat in one place and its instance in
    another."""
    asked = await session.get(Block, turn_id)
    if (
        asked is None
        or asked.conversation_id != conversation_id
        or asked.kind != BlockKind.message
        or looks_like_agent_handle(asked.author)
    ):
        return None
    said_since = select(Block.author).where(
        Block.conversation_id == conversation_id,
        Block.kind == BlockKind.message,
        Block.created_at > asked.created_at,
    )
    answered = await session.scalar(
        select(said_since.where(agent_handle_column(Block.author)).exists())
    )
    if answered:
        return None
    others = await session.scalar(
        select(
            said_since.where(
                Block.author != asked.author, ~agent_handle_column(Block.author)
            ).exists()
        )
    )
    return asked.id if others else None


async def persist_assistant_message(
    sessions: async_sessionmaker[AsyncSession],
    *,
    load_topic: TopicLoader,
    prepare_mentions: AssistantMentions,
    notify_mentions: MentionNotifier,
    resolve_author: EventAuthorResolver,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    text: str,
    turn_id: uuid.UUID | None,
    reply_to: uuid.UUID | None,
    roster: list[dict] | None,
    topic_refs: list[dict],
    room: Topic | None = None,
    eid: str | None = None,
    eids: tuple[str, ...] = (),
    platform_unsolicited: bool = False,
    continuation_id: uuid.UUID | None = None,
    at: datetime | None = None,
    inner_id: uuid.UUID | None = None,
    author: str | None = None,
    publication_id: str | None = None,
    own_output: bool = False,
    extra_meta: dict | None = None,
    closing: bool = False,
    attachments: list[dict] | None = None,
) -> StoredAssistant:
    meta: dict | None = None
    if eid:
        meta = {**(meta or {}), "eid": eid}
    if len(eids) > 1:
        meta = {**(meta or {}), "eids": list(eids)}
    if platform_unsolicited:
        meta = {**(meta or {}), "platform_unsolicited": True}
    if extra_meta:
        meta = {**(meta or {}), **extra_meta}
    known_ids = [e for e in dict.fromkeys((eid, *eids)) if e]
    async with sessions() as session:
        publication_key = None
        publication_input = {
            "text": text,
            "reply_to": str(reply_to) if reply_to else None,
            # The pictures are part of what this request_id stands for: the
            # same id with a different picture is a different publication, not
            # a retry. The digest is stable across a retry; the stored path is
            # not (each upload gets its own directory).
            **(
                {"attachments": [a.get("digest") for a in attachments]}
                if attachments
                else {}
            ),
        }
        if publication_id is not None:
            publication_key = action_key(
                topic_id, "chat-publish", author or "", publication_id
            )
            if not await idem.claim(
                session,
                publication_key,
                action="chat-publish",
                scope_id=str(topic_id),
            ):
                previous = await idem.stored_result(session, publication_key)
                if previous is None or previous["input"] != publication_input:
                    raise ConflictError(say("chatPublicationIdReused"))
                return StoredAssistant(previous["block"], False, None)
        if known_ids and await any_output_event_exists(session, topic_id, known_ids):
            return StoredAssistant(None, False, None)
        # `has_any_eid` alone is a SELECT followed by an INSERT, and the same
        # hook event reaches this method from two places at once — the turn's
        # own attribution and the platform-unsolicited path. Both read "not
        # there", both write, and the room gets the message twice ~15ms
        # apart, the two rows carrying the SAME eid (measured across the
        # dev database: every duplicated 芝士 message has this shape).
        # ON CONFLICT DO NOTHING is what actually decides; the read above
        # stays because it also catches a copy landed by an earlier turn,
        # which no claim of ours would.
        if known_ids and not await idem.claim(
            session,
            action_key(topic_id, "block-eid", *sorted(known_ids)),
            action="message",
            scope_id=str(topic_id),
        ):
            return StoredAssistant(None, False, None)
        # Claim BEFORE writing, in the SAME session: the key and the block
        # commit together, so "key present" and "message posted" cannot
        # disagree no matter where the process dies.
        if continuation_id is not None and not await idem.claim(
            session,
            action_key(continuation_id, "message", text),
            action="message",
            scope_id=str(topic_id),
        ):
            return StoredAssistant(None, False, None)
        topic = room if room is not None else await load_topic(session, topic_id)
        if "@" in text or inner_id is not None:
            text, roster = await prepare_mentions(
                session, project_id, topic, text, roster, topic_refs
            )
        else:
            roster = roster or []
        if (
            closing
            and turn_id is not None
            and await last_turn_output(session, topic_id, turn_id) == text
        ):
            return StoredAssistant(None, False, None)
        author = author or await resolve_author(session, topic_id)
        if (
            reply_to is None
            and turn_id is not None
            and inner_id is None
            and topic is not None
            and not is_private_chat(topic)
            and CHECKLIST_META_KEY not in (meta or {})
        ):
            reply_to = await _question_talked_past(session, topic_id, turn_id)
        # 「关于什么」由 `inner_id` 推出，调用方不另声明：调用方说出这条事件
        # 关于什么的方式**就是**递不递一张卡下来（变更提醒从不递）。再收一个
        # about 形参，是同一个事实在一处声明两遍——不加 `about_kind` 列的同一条理由。
        landed = landing(
            EventAbout.task if inner_id is not None else EventAbout.room,
            project_id=project_id,
            room_id=topic_id,
            task_id=inner_id,
        )
        block = await append_output(
            session,
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author=author,
            author_type=AuthorType.participant,
            content=text,
            kind=BlockKind.message,
            reply_to=reply_to,
            turn_id=turn_id,
            meta=meta,
            created_at=at,
            own_output=own_output,
        )
        # 图片随消息一起落库：每张图一块 `attachment`，和消息同一个事务、同一个
        # turn。它们是这条消息的一部分（读者在气泡旁边直接看到），不是房间里另
        # 摆出来的一份文件，所以 kind 是 attachment 而不是 artifact。
        attachment_payloads: list[dict] = []
        for attachment in attachments or []:
            att_block = await append_output(
                session,
                project_id=landed.project_id,
                conversation_id=landed.conversation_id,
                author=author,
                author_type=AuthorType.participant,
                content=str(attachment.get("path") or ""),
                kind=BlockKind.attachment,
                mime_type=str(attachment.get("mime") or "") or None,
                turn_id=block.id,
                # 纯图片的一条消息也要守住回复线程（和人发图时同一条规矩）。
                reply_to=None if text else reply_to,
                created_at=at,
                own_output=own_output,
            )
            attachment_payloads.append(
                BlockOut.model_validate(att_block).model_dump(mode="json")
            )
        # <@handle> mentions in 芝士's message → strong notify (the token is
        # the single source of truth: what's shown = who's notified).
        # Hallucinated handles get flagged in 现场, never silently no-op.
        if topic is not None:
            await notify_mentions(
                session, topic, block, author, roster, flag_unresolved=True
            )
        payload = BlockOut.model_validate(block).model_dump(mode="json")
        # A reply carries a one-line preview of what it answers, so a reader
        # whose timeline does not hold the parent still sees which line this is.
        # The parent itself stays out of the page and out of the reader.
        if reply_to is not None:
            parent = await message_reply_target(session, reply_to)
            if parent is not None and parent.conversation_id == landed.conversation_id:
                preview = reply_preview(parent)
                payload["reply_preview"] = preview
                for attachment in attachment_payloads:
                    if attachment.get("reply_to") is not None:
                        attachment["reply_preview"] = preview
        if publication_key is not None:
            await idem.record_result(
                session,
                publication_key,
                {"input": publication_input, "block": payload},
            )
        await session.commit()
    return StoredAssistant(payload, True, author, tuple(attachment_payloads))
