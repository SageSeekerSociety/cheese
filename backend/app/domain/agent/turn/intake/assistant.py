"""Interpret assistant output and attribute committed publications to live work."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.cli_notices import cli_notice
from app.domain.agent.mentions import _expand_mention_names, announce_mentions
from app.domain.agent.queries import _agent_handle
from app.domain.agent.turn.intake.events import _persist_room_event
from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent.turn.store.assistant import persist_assistant_message
from app.domain.block.models import AuthorType
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
    ) -> None:
        self.sessions = sessions
        self.live = live
        self._read_roster = read_roster

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
    ) -> dict | None:
        # 有些「助手消息」根本不是芝士说的 —— 是它脚下的 CLI 把自己的英文提示
        # 当成助手输出印了出来。拦在这里而不是调用方:每一条写入路都经过这个方法,
        # 拦在门口才不会有一条漏网。
        as_notice = None if publish else cli_notice(text)
        if as_notice is not None:
            line, notice_meta = as_notice
            return await _persist_room_event(
                self.sessions,
                self.live,
                project_id=project_id,
                topic_id=topic_id,
                content=line,
                meta=notice_meta,
                turn_id=turn_id,
                eid=eid,
                platform_unsolicited=platform_unsolicited,
                in_room=True,
                author_type=AuthorType.platform,
                inner_id=inner_id,
            )
        stored = await persist_assistant_message(
            self.sessions,
            load_topic=load_topic,
            prepare_mentions=self._prepare_mentions,
            notify_mentions=announce_mentions,
            resolve_author=_agent_handle,
            project_id=project_id,
            topic_id=topic_id,
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
            publish=publish,
            author=author,
            publication_id=publication_id,
            own_output=own_output,
            extra_meta=extra_meta,
            closing=closing,
        )
        if not stored.written:
            return stored.payload
        payload = stored.payload
        assert payload is not None
        author = stored.author
        assert author is not None
        if publish:
            # The caller attributes the publication to a turn when it can; an
            # agent running off this process (a remote executor) publishes over
            # HTTP, where the runner knows no live work and hands in None. Its
            # turn still exists here, so fall back — but carefully, because a
            # room seats several agents: crediting agent A's publication to
            # agent B's turn would silence B's reminder while A's room stays
            # dark.
            # So: the publisher's own live turn first; an unambiguous single
            # live turn next (covers tokens that don't name an agent seat);
            # nothing when two agents' turns are live and neither is the
            # publisher's. Without a fallback at all, every remote publication
            # missed `last_chat_at` and the sweep kept "reminding" a turn that
            # had just spoken, counting the silence from turn start.
            conversation_id = inner_id or topic_id
            work_id = turn_id or self.live.attributed_work_id(conversation_id, author)
            state = (
                self.live.hook_work.get((conversation_id, work_id))
                if work_id is not None
                else None
            )
            if state is not None:
                state.last_chat_at = datetime.now(UTC)
                state.last_progress_reminder_at = None
        return payload
