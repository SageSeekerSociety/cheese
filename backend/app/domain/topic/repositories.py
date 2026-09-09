"""Topic data access."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import (
    ColumnElement,
    SQLColumnExpression,
    UnaryExpression,
)

from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import Block, BlockKind
from app.domain.identity.handles import CHEESE_HANDLE, CHEESE_NAME, agent_dm_key
from app.domain.project.environment import project_environment
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind, TopicProgress, TopicReadState

TopicSortField = Literal["updated_at", "title", "last_activity_at"]
SortOrder = Literal["asc", "desc"]


def _last_activity() -> ColumnElement[datetime]:
    """When something last HAPPENED in a topic — the newest block it holds.

    Derived per query instead of stored on the row, because `updated_at` cannot
    answer this: it is the topics ROW's mtime (Timestamps.onupdate), and adding
    a block writes the blocks table only, so a topic that has been talked in all
    day still reports the moment its title or session id last changed. Deriving
    it also means no backfill for the topics that already drifted, and no drift
    when blocks are deleted.

    A topic with no blocks yet falls back to its own creation — "nothing has
    happened since it was made" is the truth for a room nobody has spoken in,
    and it keeps the value non-null so sorting and filtering stay total.

    Threads COUNT here, and that is deliberate: a room whose work is running is
    alive, and the normal state of such a room is that its own line is quiet.
    Note this is the opposite call from `unread_counts` below — same join, same
    two tables, opposite answer, because "is this place alive" and "is there
    something here for me to read" are different questions.
    """
    newest_block = (
        select(func.max(Block.created_at))
        .where(Block.topic_id == Topic.id)
        .correlate(Topic)
        .scalar_subquery()
    )
    return func.coalesce(newest_block, Topic.created_at)


def _order_by(sort: TopicSortField | None, order: SortOrder) -> UnaryExpression:
    column: SQLColumnExpression[object]
    if sort == "title":
        column = Topic.title
    elif sort == "updated_at":
        column = Topic.updated_at
    elif sort == "last_activity_at":
        column = _last_activity()
    else:
        column = Topic.created_at
    return column.desc() if order == "desc" else column.asc()


class TopicRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        parent_id: uuid.UUID | None = None,
        kind: TopicKind = TopicKind.topic,
        created_by: str | None = None,
        upgraded_from_block_id: uuid.UUID | None = None,
        agent_instance_id: uuid.UUID | None = None,
    ) -> Topic:
        project = await self._session.get(Project, project_id)
        topic = Topic(
            project_id=project_id,
            environment=project_environment(project.settings if project else None),
            title=title,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
            upgraded_from_block_id=upgraded_from_block_id,
            agent_instance_id=agent_instance_id,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.refresh(topic)
        return topic

    async def get(self, topic_id: uuid.UUID) -> Topic | None:
        return await self._session.get(Topic, topic_id)

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        sort: TopicSortField | None = None,
        order: SortOrder = "asc",
        active_since: datetime | None = None,
    ) -> list[Topic]:
        """The project's topic tree, flat.

        ``active_since`` keeps only topics whose last activity (see
        ``_last_activity``) is at or after that instant — "最近活跃的话题". It
        filters the flat list, so a kept topic's parent may be filtered out;
        callers that rebuild the tree should not combine it with the filter.
        """
        stmt = self._project_topics_stmt(
            project_id, sort=sort, order=order, active_since=active_since
        )
        return list((await self._session.scalars(stmt)).all())

    def _project_topics_stmt(
        self,
        project_id: uuid.UUID,
        *,
        sort: TopicSortField | None,
        order: SortOrder,
        active_since: datetime | None,
    ) -> Select[tuple[Topic]]:
        """The one definition of "this project's topic tree, flat, in order".

        Shared so ``list_for_project`` and ``list_for_project_with_activity``
        cannot drift into filtering or ordering the same list differently.
        """
        # Private chats are not part of the topic tree.
        stmt = select(Topic).where(
            Topic.project_id == project_id, Topic.is_private.is_(False)
        )
        if active_since is not None:
            stmt = stmt.where(_last_activity() >= active_since)
        return stmt.order_by(_order_by(sort, order))

    async def list_for_project_with_activity(
        self,
        project_id: uuid.UUID,
        *,
        sort: TopicSortField | None = None,
        order: SortOrder = "asc",
        active_since: datetime | None = None,
    ) -> list[tuple[Topic, datetime]]:
        """The same list, each row paired with its 最后活动时间 — in ONE query.

        The list endpoint needs both, and asking for them separately made the
        database derive ``_last_activity`` twice over the same topics: once to
        sort by it, once to report it. Selecting it alongside the rows it
        already sorted costs nothing extra, because it is the expression the
        ORDER BY evaluates anyway.

        Separate from ``list_for_project`` rather than replacing it: that one's
        ``list[Topic]`` is what mentions, the agent's context builders and the
        dashboard want, and none of them look at last activity.
        """
        stmt = self._project_topics_stmt(
            project_id, sort=sort, order=order, active_since=active_since
        ).add_columns(_last_activity())
        rows = (await self._session.execute(stmt)).all()
        return [(topic, last) for topic, last in rows]

    async def last_activity_for_topics(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """{topic_id: last activity} for a batch of topics, in ONE query.

        For callers holding topics they did not get from
        ``list_for_project_with_activity`` — a single room's header opened by
        deep link, which has one topic and no list to have derived it with.
        A caller that is about to list a project's topics should use that
        method instead and get both from one query.
        """
        if not topic_ids:
            return {}
        stmt = select(Topic.id, _last_activity()).where(Topic.id.in_(topic_ids))
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: last for topic_id, last in rows}

    async def get_or_create_private(
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        peer_handle: str | None = None,
        agent_instance_id: uuid.UUID | None = None,
        agent_display_name: str | None = None,
    ) -> Topic:
        """A 1:1 private conversation in this project.

        With ``peer_handle`` it is a person-to-person DM between the two humans;
        the unordered pair is canonicalized (owner = min, peer = max) so both
        participants get and share the same row regardless of who opens it.

        Without it, it is the member's 1:1 with ONE AI teammate, identified by
        ``agent_instance_id`` — the same column a room uses to say which teammate
        works in it, which is why nothing else has to change for that teammate's
        role and model to be the ones answering here. One room per (member,
        teammate) pair: a project with three teammates gives each member three,
        and switching the project's default does not move any of them.
        """
        if peer_handle is not None:
            owner, peer = sorted((user_handle, peer_handle))
            stmt = select(Topic).where(
                Topic.project_id == project_id,
                Topic.is_private.is_(True),
                Topic.private_owner == owner,
                Topic.private_peer == peer,
            )
        else:
            owner, peer = user_handle, None
            stmt = select(Topic).where(
                Topic.project_id == project_id,
                Topic.is_private.is_(True),
                Topic.private_owner == user_handle,
                Topic.private_peer.is_(None),
                Topic.agent_instance_id == agent_instance_id,
            )
        existing = (await self._session.scalars(stmt)).first()
        if existing is not None:
            return existing
        # Title is a rendering hint only; the sidebar/ChatPanel show the peer's
        # own name from the roster. Deterministic, no NL parsing (CLAUDE.md §4).
        if peer:
            title = f"私聊 · {owner} · {peer}"
        else:
            title = f"与{agent_display_name or CHEESE_NAME}私聊 · {owner}"
        project = await self._session.get(Project, project_id)
        topic = Topic(
            project_id=project_id,
            environment=project_environment(project.settings if project else None),
            title=title,
            kind=TopicKind.topic,
            created_by=user_handle,
            is_private=True,
            private_owner=owner,
            private_peer=peer,
            agent_instance_id=agent_instance_id,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.refresh(topic)
        return topic

    async def pin_unpinned_agent_dms(
        self,
        project_id: uuid.UUID,
        agent_instance_id: uuid.UUID,
        *,
        user_handle: str | None = None,
    ) -> None:
        """Give the teammate-less 芝士 DMs the teammate they have been talking
        to all along.

        A DM opened before there was one room per teammate names no teammate, so
        it is answered by whatever the project's default is at the time. Callers
        pass the agent that is the default *right now*, and call this at the two
        moments that answer could change — somebody opens the DM, or the project
        picks a different default. Writing it down at those two points is what
        makes the conversation stay where it is: under the teammate that
        actually held it, not under whoever holds the default later.

        ``user_handle`` narrows it to one member's DM; without it, every
        unpinned DM in the project (what the default moving is about).
        """
        await self._session.execute(
            update(Topic)
            .where(
                Topic.project_id == project_id,
                Topic.is_private.is_(True),
                Topic.private_peer.is_(None),
                Topic.agent_instance_id.is_(None),
                *(
                    [Topic.private_owner == user_handle]
                    if user_handle is not None
                    else []
                ),
            )
            .values(agent_instance_id=agent_instance_id)
        )

    async def list_children(self, parent_id: uuid.UUID) -> list[Topic]:
        stmt = (
            select(Topic).where(Topic.parent_id == parent_id).order_by(Topic.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def archival_for_project(
        self, project_id: uuid.UUID
    ) -> dict[uuid.UUID, datetime | None]:
        """Every topic of the project — private chats included, since they have
        directories too — mapped to when it was archived (None while active).
        What the storage sweep reconciles the disk against."""
        stmt = select(Topic.id, Topic.archived_at).where(Topic.project_id == project_id)
        return dict((await self._session.execute(stmt)).tuples().all())

    async def mark_transcripts_archived(
        self, topic_id: uuid.UUID, at: datetime
    ) -> bool:
        """Record that the topic's raw session files reached the platform.
        False when no topic has this id."""
        stamped = await self._session.execute(
            update(Topic)
            .where(Topic.id == topic_id)
            .values(transcripts_archived_at=at)
            .returning(Topic.id)
        )
        return stamped.scalar() is not None

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Topic)
            .where(Topic.project_id == project_id, Topic.is_private.is_(False))
        )
        return int((await self._session.scalar(stmt)) or 0)

    # ---- 话题级未读 (Feishu-style badges) -------------------------------

    async def unread_counts(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, int]:
        """Unread message count per topic for one user, in one query.

        Unread = message blocks authored by OTHERS on the room's OWN line,
        created after the user's read cursor (no cursor = all of them). Only
        kind=message counts — doc edits / events / decisions have their own
        surfaces. Other people's private chats are excluded.

        Threads are excluded (`task_id IS NULL`), and that is the opposite call
        from `last_activity_at` one screen over, which DOES count them. The two
        answer different questions: a room with work running in it is alive and
        should sort up, but a badge that lights every time any 分身 says anything
        is a badge people learn to ignore. Reading the room does not mean you
        read every thread in it either — the cursor is the room's.
        """
        stmt = (
            select(Block.topic_id, func.count())
            .join(Topic, Topic.id == Block.topic_id)
            .outerjoin(
                TopicReadState,
                and_(
                    TopicReadState.topic_id == Block.topic_id,
                    TopicReadState.user_handle == user_handle,
                ),
            )
            .where(
                Topic.project_id == project_id,
                or_(
                    Topic.is_private.is_(False),
                    Topic.private_owner == user_handle,
                    Topic.private_peer == user_handle,
                ),
                Block.kind == BlockKind.message,
                Block.task_id.is_(None),
                Block.author != user_handle,
                or_(
                    TopicReadState.last_read_at.is_(None),
                    Block.created_at > TopicReadState.last_read_at,
                ),
            )
            .group_by(Block.topic_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: int(count) for topic_id, count in rows}

    async def private_unread_counts(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[str, int]:
        """Unread count per 私聊, keyed by the OTHER party.

        Same definition of "unread" as :meth:`unread_counts` — the two differ
        only in how the caller addresses a row. Private chats are not in the
        topic tree, so the roster page renders one DM row per member and per AI
        teammate and never learns the conversation's topic id; a map keyed by
        topic id is therefore unusable there. A person is keyed by their handle,
        an AI teammate by ``agent_dm_key`` — see there for why a teammate does
        not get to use the bare handle.

        A DM that names no teammate predates one-room-per-teammate and is still
        answered by whatever the project's default is, so that is what it counts
        against; opening it pins it (:meth:`pin_unpinned_agent_dm`) and this
        stops being a question.
        """
        stmt = (
            select(
                Topic.private_owner,
                Topic.private_peer,
                AgentInstance.handle,
                func.count(),
            )
            .select_from(Topic)
            .join(Block, Block.topic_id == Topic.id)
            .join(Project, Project.id == Topic.project_id)
            .outerjoin(
                AgentInstance,
                AgentInstance.id
                == func.coalesce(
                    Topic.agent_instance_id, Project.default_agent_instance_id
                ),
            )
            .outerjoin(
                TopicReadState,
                and_(
                    TopicReadState.topic_id == Topic.id,
                    TopicReadState.user_handle == user_handle,
                ),
            )
            .where(
                Topic.project_id == project_id,
                Topic.is_private.is_(True),
                or_(
                    Topic.private_owner == user_handle,
                    Topic.private_peer == user_handle,
                ),
                Block.kind == BlockKind.message,
                # A private room has threads too — resolving an upstream
                # conflict opens one there — and the same rule applies: the
                # badge is about the room's own line.
                Block.task_id.is_(None),
                Block.author != user_handle,
                or_(
                    TopicReadState.last_read_at.is_(None),
                    Block.created_at > TopicReadState.last_read_at,
                ),
            )
            .group_by(
                Topic.id, Topic.private_owner, Topic.private_peer, AgentInstance.handle
            )
        )
        rows = (await self._session.execute(stmt)).all()
        counts: dict[str, int] = {}
        for owner, peer, agent_handle, count in rows:
            if peer is None:
                key = agent_dm_key(agent_handle or CHEESE_HANDLE)
            else:
                key = peer if owner == user_handle else owner
            counts[key] = counts.get(key, 0) + int(count)
        return counts

    async def mark_read(self, topic_id: uuid.UUID, user_handle: str) -> None:
        """Bump the user's read cursor on a topic to now (upsert)."""
        stmt = select(TopicReadState).where(
            TopicReadState.topic_id == topic_id,
            TopicReadState.user_handle == user_handle,
        )
        state = (await self._session.scalars(stmt)).first()
        now = datetime.now(UTC)
        if state is None:
            self._session.add(
                TopicReadState(
                    topic_id=topic_id, user_handle=user_handle, last_read_at=now
                )
            )
        else:
            state.last_read_at = now
        await self._session.flush()


class TopicProgressRepository:
    """进度层 storage: one checklist per place — a room's main line, or a
    thread in it (#187)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(
        self, topic_id: uuid.UUID, *, task_id: uuid.UUID | None = None
    ) -> TopicProgress | None:
        stmt = select(TopicProgress).where(
            TopicProgress.topic_id == topic_id,
            TopicProgress.task_id.is_(None)
            if task_id is None
            else TopicProgress.task_id == task_id,
        )
        return (await self._session.scalars(stmt)).first()

    async def save(
        self,
        topic_id: uuid.UUID,
        items: list[dict],
        *,
        task_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
    ) -> TopicProgress:
        """Overwrite this place's checklist (upsert).

        Current state, not history — the conversation timeline is where history
        lives. Callers hand over a fresh list each time; the row is rewritten so
        a reader never sees a half-applied checklist.

        Looked up by (room, thread) rather than by primary key: `topic_id` used
        to BE the key and cannot be any more, because a thread's row is
        identified by the pair and a primary key cannot hold the NULL that means
        "the room's own main line".
        """
        row = await self.get(topic_id, task_id=task_id)
        if row is None:
            row = TopicProgress(
                topic_id=topic_id, task_id=task_id, items=[], turn_id=turn_id
            )
            self._session.add(row)
        # Rebind rather than mutate: SQLAlchemy does not track in-place edits of
        # a plain JSON column, so an appended item would silently not be saved.
        row.items = [dict(item) for item in items]
        row.turn_id = turn_id
        await self._session.flush()
        return row
