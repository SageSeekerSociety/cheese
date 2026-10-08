"""Assistant idempotency and durable writes, with same-transaction SQL effects."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ConflictError
from app.domain.agent.turn.store.events import EventAuthorResolver
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.topic.models import Topic
from app.domain.topic.repositories import TopicRepository


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


async def persist_assistant_message(
    sessions: async_sessionmaker[AsyncSession],
    *,
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
    eid: str | None = None,
    eids: tuple[str, ...] = (),
    platform_unsolicited: bool = False,
    continuation_id: uuid.UUID | None = None,
    at: datetime | None = None,
    inner_id: uuid.UUID | None = None,
    publish: bool = False,
    author: str | None = None,
    publication_id: str | None = None,
    own_output: bool = False,
    extra_meta: dict | None = None,
    closing: bool = False,
) -> StoredAssistant:
    as_progress = not publish
    meta: dict | None = {"in_room": False, "progress": True} if as_progress else None
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
        blocks = BlockRepository(session)
        publication_key = None
        publication_input = {
            "text": text,
            "reply_to": str(reply_to) if reply_to else None,
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
                    raise ConflictError("request_id was used for another message")
                return StoredAssistant(previous["block"], False, None)
        if known_ids and await blocks.has_any_eid(topic_id, known_ids):
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
            action_key(continuation_id, "progress" if as_progress else "message", text),
            action="message",
            scope_id=str(topic_id),
        ):
            return StoredAssistant(None, False, None)
        topic = await TopicRepository(session).get(topic_id)
        text, roster = await prepare_mentions(
            session, project_id, topic, text, roster, topic_refs
        )
        if (
            closing
            and turn_id is not None
            and await blocks.last_said_in_turn(topic_id, turn_id) == text
        ):
            return StoredAssistant(None, False, None)
        author = author or await resolve_author(session, topic_id)
        # 「关于什么」由 `inner_id` 推出，调用方不另声明：调用方说出这条事件
        # 关于什么的方式**就是**递不递一张卡下来（变更提醒从不递）。再收一个
        # about 形参，是同一个事实在一处声明两遍——不加 `about_kind` 列的同一条理由。
        landed = landing(
            EventAbout.task if inner_id is not None else EventAbout.room,
            project_id=project_id,
            room_id=topic_id,
            task_id=inner_id,
        )
        block = await blocks.add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author=author,
            author_type=AuthorType.participant,
            content=text,
            kind=BlockKind.event if as_progress else BlockKind.message,
            reply_to=reply_to,
            turn_id=turn_id,
            meta=meta,
            created_at=at,
            own_output=own_output,
        )
        # <@handle> mentions in 芝士's message → strong notify (the token is
        # the single source of truth: what's shown = who's notified).
        # Hallucinated handles get flagged in 现场, never silently no-op.
        if topic is not None and not as_progress:
            await notify_mentions(
                session, topic, block, author, roster, flag_unresolved=True
            )
        payload = BlockOut.model_validate(block).model_dump(mode="json")
        if publication_key is not None:
            await idem.record_result(
                session,
                publication_key,
                {"input": publication_input, "block": payload},
            )
        await session.commit()
    return StoredAssistant(payload, True, author)
