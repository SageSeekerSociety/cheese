"""Interpret assistant output and attribute committed publications to live work."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.mentions import _expand_mention_names, announce_mentions
from app.domain.agent.queries import _agent_handle
from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent.turn.store.assistant import (
    persist_assistant_message,
)
from app.domain.agent.turn.store.assistant import (
    stored_publication as find_stored_publication,
)
from app.domain.topic.models import Topic
from app.domain.topic.reads import load_topic


class AssistantMessages:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        live: LiveWork,
        read_roster: Callable[
            [AsyncSession, uuid.UUID, Topic | None], Awaitable[list[dict]]
        ],
        thread_replied: Callable[[uuid.UUID], Awaitable[None]],
    ) -> None:
        self.sessions = sessions
        self.live = live
        self._read_roster = read_roster
        self._thread_replied = thread_replied

    async def _prepare_mentions(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        topic: Topic | None,
        text: str,
        roster: list[dict] | None,
        topic_refs: list[dict],
    ) -> tuple[str, list[dict]]:
        if roster is None:
            # None means backfill has no roster; [] means private, not missing.
            roster = await self._read_roster(session, project_id, topic)
        return _expand_mention_names(text, roster, topic_refs), roster

    async def stored_publication(
        self, *, topic_id: uuid.UUID, author: str, publication_id: str
    ) -> dict | None:
        """The result this publication id already stored, if it stored one.

        The retry path reads this before it names any attachment, so a retry
        of a message the room already has writes no second copy of the bytes."""
        return await find_stored_publication(
            self.sessions,
            topic_id=topic_id,
            author=author,
            publication_id=publication_id,
        )

    async def persist_assistant_message(
        self,
        *,
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
    ) -> dict | None:
        payload, _ = await self._persist(
            project_id=project_id,
            topic_id=topic_id,
            room=room,
            text=text,
            turn_id=turn_id,
            reply_to=reply_to,
            roster=roster,
            topic_refs=topic_refs,
            eid=eid,
            eids=eids,
            platform_unsolicited=platform_unsolicited,
            continuation_id=continuation_id,
            at=at,
            inner_id=inner_id,
            author=author,
            publication_id=publication_id,
            own_output=own_output,
            extra_meta=extra_meta,
            closing=closing,
        )
        return payload

    async def persist_assistant_message_with_attachments(
        self,
        *,
        attachments: list[dict],
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        text: str,
        turn_id: uuid.UUID | None,
        reply_to: uuid.UUID | None,
        roster: list[dict] | None,
        topic_refs: list[dict],
        inner_id: uuid.UUID | None = None,
        author: str | None = None,
        publication_id: str | None = None,
        own_output: bool = False,
        extra_meta: dict | None = None,
        closing: bool = False,
    ) -> tuple[dict | None, list[dict]]:
        """A publication that carries pictures: the message and its attachment
        blocks are written in one transaction, and both payloads come back so
        the caller can broadcast them in send order."""
        return await self._persist(
            project_id=project_id,
            topic_id=topic_id,
            text=text,
            turn_id=turn_id,
            reply_to=reply_to,
            roster=roster,
            topic_refs=topic_refs,
            inner_id=inner_id,
            author=author,
            publication_id=publication_id,
            own_output=own_output,
            extra_meta=extra_meta,
            closing=closing,
            attachments=attachments,
        )

    async def _persist(
        self,
        *,
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
    ) -> tuple[dict | None, list[dict]]:
        stored = await persist_assistant_message(
            self.sessions,
            load_topic=load_topic,
            prepare_mentions=self._prepare_mentions,
            notify_mentions=announce_mentions,
            resolve_author=_agent_handle,
            project_id=project_id,
            topic_id=topic_id,
            room=room,
            text=text,
            turn_id=turn_id,
            reply_to=reply_to,
            roster=roster,
            topic_refs=topic_refs,
            eid=eid,
            eids=eids,
            platform_unsolicited=platform_unsolicited,
            continuation_id=continuation_id,
            at=at,
            inner_id=inner_id,
            author=author,
            publication_id=publication_id,
            own_output=own_output,
            extra_meta=extra_meta,
            closing=closing,
            attachments=attachments,
        )
        if not stored.written:
            return stored.payload, []
        payload = stored.payload
        assert payload is not None
        author = stored.author
        assert author is not None
        if inner_id is not None:
            # A published message is what a 支线 counts (progress lines are
            # events and do not), and both of its writers — `chat_send` and a
            # posted checklist — pass through here, so the channel hears its
            # 支线 grew from this one place. Without it the line under the
            # message keeps the count it was read with.
            await self._thread_replied(inner_id)
        return payload, list(stored.attachments)
