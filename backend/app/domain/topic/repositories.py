"""Topic data access."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import (
    JSON,
    Select,
    String,
    Uuid,
    and_,
    case,
    cast,
    column,
    func,
    or_,
    select,
    table,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import (
    ColumnElement,
    SQLColumnExpression,
    UnaryExpression,
)

from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    agent_dm_key,
    agent_handle_column,
    agent_instance_handle,
    looks_like_agent_handle,
)
from app.domain.project.environment import project_environment
from app.domain.project.models import Project
from app.domain.topic.models import (
    PLACEHOLDER_TITLE,
    TitleSource,
    Topic,
    TopicKind,
    TopicMembership,
    TopicProgress,
    TopicReadState,
)

TopicSortField = Literal["updated_at", "title", "last_activity_at"]
SortOrder = Literal["asc", "desc"]


# Which room a task is in, and who takes part in it. A bare table: `room_task`
# depends on this domain.
_tasks = table(
    "tasks",
    column("id", Uuid),
    column("room_id", Uuid),
    column("project_id", Uuid),
    column("status", String),
    column("owner_handle", String),
    column("contributor_handles", JSON),
)


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

    Tasks COUNT here, and that is deliberate: a room whose work is running is
    alive, and the normal state of such a room is that its own line is quiet.
    Note this is the opposite call from `unread_counts` below — same join, same
    two tables, opposite answer, because "is this place alive" and "is there
    something here for me to read" are different questions.
    """
    # Two maxima rather than one over "the room or any of its tasks": each is an
    # equality on `conversation_id`, which `ix_blocks_conversation_created_at`
    # answers from its last entry; an OR across the two would read every block
    # of the room. GREATEST skips a NULL.
    own = (
        select(func.max(Block.created_at))
        .where(Block.conversation_id == Topic.id)
        .correlate(Topic)
        .scalar_subquery()
    )
    in_tasks = (
        select(func.max(Block.created_at))
        .join(_tasks, _tasks.c.id == Block.conversation_id)
        .where(_tasks.c.room_id == Topic.id)
        .correlate(Topic)
        .scalar_subquery()
    )
    return func.coalesce(func.greatest(own, in_tasks), Topic.created_at)


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


def _readable_by(user_handle: str):
    """Rooms a person's badge maps may name: every non-private room, and the
    private rooms they still hold a seat in. One clause for both the unread map
    and the notify-level map, so the two can never disagree about a room."""
    return or_(
        Topic.is_private.is_(False),
        Topic.id.in_(
            select(TopicMembership.topic_id).where(
                TopicMembership.member_handle == user_handle
            )
        ),
    )


class TopicRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        title: str | None,
        parent_id: uuid.UUID | None = None,
        kind: TopicKind = TopicKind.topic,
        created_by: str | None = None,
        upgraded_from_block_id: uuid.UUID | None = None,
    ) -> Topic:
        """``title=None`` is a room nobody has named: it is stored under the
        placeholder, and ``title_source`` — not the text — is what says so.
        A room created with a name was named by whoever created it, even when
        that name happens to read like the placeholder."""
        project = await self._session.get(Project, project_id)
        title = (title or "").strip() or None
        topic = Topic(
            project_id=project_id,
            environment=project_environment(project.settings if project else None),
            title=title or PLACEHOLDER_TITLE,
            title_source=(
                TitleSource.placeholder if title is None else TitleSource.human
            ),
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
            upgraded_from_block_id=upgraded_from_block_id,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.refresh(topic)
        return topic

    async def get(self, topic_id: uuid.UUID) -> Topic | None:
        return await self._session.get(Topic, topic_id)

    async def lock(self, topic_id: uuid.UUID) -> Topic | None:
        return (
            await self._session.scalars(
                select(Topic)
                .where(Topic.id == topic_id)
                # Serialize execution admission with archival while allowing
                # hook writes whose foreign keys only take KEY SHARE.
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()

    async def lock_all_in_project(self, project_id: uuid.UUID) -> list[Topic]:
        """Every topic of the project, private chats included, locked the way
        :meth:`lock` locks one — what archiving the whole project walks."""
        return list(
            (
                await self._session.scalars(
                    select(Topic)
                    .where(Topic.project_id == project_id)
                    .order_by(Topic.created_at)
                    .with_for_update(key_share=True)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

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
            [project_id], sort=sort, order=order, active_since=active_since
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_for_projects(self, project_ids: list[uuid.UUID]) -> list[Topic]:
        """同一棵树，跨若干个项目 —— 「待我处理」要问的是我能看见的全部项目。

        私聊照样不在里面（见 `_project_topics_stmt`）：它不是话题树的一部分，也从
        来不会有验收卡或者待确认问题挂在上面。
        """
        if not project_ids:
            return []
        stmt = select(Topic).where(
            Topic.project_id.in_(project_ids), Topic.is_private.is_(False)
        )
        return list((await self._session.scalars(stmt)).all())

    def _project_topics_stmt(
        self,
        project_ids: list[uuid.UUID],
        *,
        sort: TopicSortField | None,
        order: SortOrder,
        active_since: datetime | None,
    ) -> Select[tuple[Topic]]:
        """The one definition of "these projects' topic trees, flat, in order".

        Shared so ``list_for_project``, ``list_for_project_with_activity`` and
        ``names_in_projects`` cannot drift into filtering or ordering the same
        list differently.
        """
        # Private chats are not part of the topic tree.
        stmt = select(Topic).where(
            Topic.project_id.in_(project_ids), Topic.is_private.is_(False)
        )
        if active_since is not None:
            stmt = stmt.where(_last_activity() >= active_since)
        return stmt.order_by(_order_by(sort, order))

    async def names_in_projects(self, project_ids: list[uuid.UUID]) -> list[Topic]:
        """Every topic of these projects, private chats left out, newest activity
        first — the rows the sidebar would list, for a name search across them."""
        if not project_ids:
            return []
        stmt = self._project_topics_stmt(
            project_ids, sort="last_activity_at", order="desc", active_since=None
        )
        return list(await self._session.scalars(stmt))

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
            [project_id], sort=sort, order=order, active_since=active_since
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
        self, *, project_id: uuid.UUID, owner: str, peer: str, title: str
    ) -> Topic:
        """The 1:1 private conversation between ``owner`` and ``peer`` in this
        project, created with ``title`` if it does not exist yet.

        Who the two are — two people in canonical order, or a person and an AI
        teammate's seat — is the service's decision; here a DM is two handles.

        Found by its two seats, because that is where they live (结论 19): the
        private room of this project that seats both handles. Two memberships,
        each asked for on its own, so which of the two opens the conversation
        does not decide whether it is found.

        名册长到三席的房间也认，认的是同一间。多出一席这间房确实答不出对面是谁，
        但那件事有它自己的出处（`TopicMemberService.private_seats` 答 None，调用方
        退回项目默认那位），而这里只答「再打开的是不是同一间」。不认它就等于每次
        打开都新开一间：私聊不进话题树（`TopicService.list_for_project` 把它们藏
        起来），角标又被 `private_unread_counts` 的两席闸滤掉，旧那间房在界面上
        一个入口都没有，里面的对话就此找不回来。

        同时匹配上好几间时次序是定死的：还是两席的那间先，然后按建的时间。没有
        `order_by` 的 `.first()` 返回哪一间没有定数，同一个人两次打开可能落进两间
        不同的房。两席那一项不是把闸装回来，是在好几间都坐着这两位的时候挑出哪一
        间才是这两位的 DM：第三个人被加进 A 和 B 的那间房之后，A 打开与他的私聊，
        两间都匹配得上，而只有一间是 A 和他的。
        """

        def seats(handle: str) -> Select[tuple[uuid.UUID]]:
            return select(TopicMembership.topic_id).where(
                TopicMembership.member_handle == handle
            )

        seat_count = (
            select(func.count())
            .select_from(TopicMembership)
            .where(TopicMembership.topic_id == Topic.id)
            .scalar_subquery()
        )
        stmt = (
            select(Topic)
            .where(
                Topic.project_id == project_id,
                Topic.is_private.is_(True),
                Topic.id.in_(seats(owner)),
                Topic.id.in_(seats(peer)),
            )
            .order_by(case((seat_count == 2, 0), else_=1), Topic.created_at)
        )
        existing = (await self._session.scalars(stmt)).first()
        if existing is not None:
            return existing
        project = await self._session.get(Project, project_id)
        topic = Topic(
            project_id=project_id,
            environment=project_environment(project.settings if project else None),
            title=title,
            kind=TopicKind.topic,
            title_source=TitleSource.human,
            created_by=owner,
            is_private=True,
        )
        self._session.add(topic)
        await self._session.flush()
        await self._session.refresh(topic)
        return topic

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
        """Unread message count per conversation for one user: rooms and tasks.

        Unread = message blocks authored by OTHERS in that conversation, created
        after the user's read cursor on it (no cursor = all of them). Only
        kind=message counts — doc edits / events / weeklies have their own
        surfaces. Other people's private chats are excluded.

        A task counts only for the people who take part in it (its owner and
        collaborators), and only what PEOPLE said there: the AI teammate talks
        on every step, and a badge that lights each time is a badge nobody
        reads. When it needs the person, the task's own mark says so. Everyone
        else follows a task they do not take part in from the channel.
        """
        rooms = (
            select(Block.conversation_id, func.count())
            .join(Topic, Topic.id == Block.conversation_id)
            .outerjoin(
                TopicReadState,
                and_(
                    TopicReadState.topic_id == Block.conversation_id,
                    TopicReadState.user_handle == user_handle,
                ),
            )
            .where(
                Topic.project_id == project_id,
                _readable_by(user_handle),
                Block.kind == BlockKind.message,
                Block.author != user_handle,
                or_(
                    TopicReadState.last_read_at.is_(None),
                    Block.created_at > TopicReadState.last_read_at,
                ),
            )
            .group_by(Block.conversation_id)
        )
        tasks = (
            select(Block.conversation_id, func.count())
            .join(_tasks, _tasks.c.id == Block.conversation_id)
            .outerjoin(
                TopicReadState,
                and_(
                    TopicReadState.topic_id == Block.conversation_id,
                    TopicReadState.user_handle == user_handle,
                ),
            )
            .where(
                _tasks.c.project_id == project_id,
                _tasks.c.status == "open",
                or_(
                    _tasks.c.owner_handle == user_handle,
                    cast(_tasks.c.contributor_handles, JSONB).contains([user_handle]),
                ),
                Block.kind == BlockKind.message,
                Block.author != user_handle,
                Block.author_type == AuthorType.participant,
                ~agent_handle_column(Block.author),
                or_(
                    TopicReadState.last_read_at.is_(None),
                    Block.created_at > TopicReadState.last_read_at,
                ),
            )
            .group_by(Block.conversation_id)
        )
        counts: dict[uuid.UUID, int] = {}
        for stmt in (rooms, tasks):
            for conversation_id, count in (await self._session.execute(stmt)).all():
                counts[conversation_id] = int(count)
        return counts

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

        A teammate sits in a DM under its seat handle, the way a person sits
        under theirs; the key the UI wants is the teammate's own handle, so the
        seat is mapped back through the project's saved teammates. A seat that
        is not a saved teammate's (a room-derived seat from before teammates had
        seats of their own) is answered by the project's default and counts
        against it.

        「哪些私聊是我的」和「对面是谁」都从名册上取（结论 19）：我的那一席把房间
        选出来，另一席就是对面。两席都在同一张表上，所以这是一次自连接，不是第二
        张表。

        「恰好两席」是这里的闸，和 `TopicMemberService.private_seats` 同一条：
        「不是我」的席位不止一个，这间房就答不出对面是谁：没有这道闸，三席的房
        间每一个「不是我」的席位各算一遍，同样的未读数翻一倍，还凭空多出一行归给
        别人的角标。答不出就一条不报，与那个读点在同一间房上答案一致。
        """
        seats = {
            agent_instance_handle(row.id): row.handle
            for row in (
                await self._session.scalars(
                    select(AgentInstance).where(AgentInstance.project_id == project_id)
                )
            ).all()
        }
        project = await self._session.get(Project, project_id)
        default_handle = CHEESE_HANDLE
        if project is not None and project.default_agent_instance_id is not None:
            default = await self._session.get(
                AgentInstance, project.default_agent_instance_id
            )
            if default is not None:
                default_handle = default.handle
        mine = aliased(TopicMembership)
        theirs = aliased(TopicMembership)
        stmt = (
            select(theirs.member_handle, func.count())
            .select_from(Topic)
            .join(
                mine,
                and_(mine.topic_id == Topic.id, mine.member_handle == user_handle),
            )
            .join(
                theirs,
                and_(
                    theirs.topic_id == Topic.id,
                    theirs.member_handle != user_handle,
                ),
            )
            .join(Block, Block.conversation_id == Topic.id)
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
                select(func.count())
                .select_from(TopicMembership)
                .where(TopicMembership.topic_id == Topic.id)
                .scalar_subquery()
                == 2,
                Block.kind == BlockKind.message,
                # A private room has tasks too — resolving an upstream
                # conflict opens one there — and the same rule applies: the
                # badge is about the room's own conversation, which the join
                # on `conversation_id` already picks.
                Block.author != user_handle,
                or_(
                    TopicReadState.last_read_at.is_(None),
                    Block.created_at > TopicReadState.last_read_at,
                ),
            )
            .group_by(theirs.member_handle)
        )
        rows = (await self._session.execute(stmt)).all()
        counts: dict[str, int] = {}
        for other, count in rows:
            if looks_like_agent_handle(other):
                key = agent_dm_key(seats.get(other, default_handle))
            else:
                key = other
            counts[key] = counts.get(key, 0) + int(count)
        return counts

    async def notify_levels(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, str]:
        """The user's non-default notification levels in a project:
        {topic_id: level}. Rooms at the default (`all`) are omitted."""
        stmt = (
            select(TopicReadState.topic_id, TopicReadState.notify_level)
            .join(Topic, Topic.id == TopicReadState.topic_id)
            .where(
                Topic.project_id == project_id,
                # 同 unread_counts 的读权限：被请出去的私密房间，我当年的静音记录
                # 还在，但它的 id 不能再告诉我。
                _readable_by(user_handle),
                TopicReadState.user_handle == user_handle,
                TopicReadState.notify_level != "all",
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return {topic_id: level for topic_id, level in rows}

    async def set_notify_level(
        self, topic_id: uuid.UUID, user_handle: str, level: str
    ) -> None:
        """Set the user's notification level on a topic (upsert). A room the
        user never opened gets a cursor at the epoch — the same as no cursor,
        so muting a room does not mark it read."""
        stmt = select(TopicReadState).where(
            TopicReadState.topic_id == topic_id,
            TopicReadState.user_handle == user_handle,
        )
        state = (await self._session.scalars(stmt)).first()
        if state is None:
            self._session.add(
                TopicReadState(
                    topic_id=topic_id,
                    user_handle=user_handle,
                    last_read_at=datetime(1970, 1, 1, tzinfo=UTC),
                    notify_level=level,
                )
            )
        else:
            state.notify_level = level
        await self._session.flush()

    async def mark_read_many(
        self, topic_ids: list[uuid.UUID], user_handle: str
    ) -> None:
        """Bump the user's read cursor on many topics to now, in one statement
        (「全部标为已读」). Rows that exist keep their notify level."""
        if not topic_ids:
            return
        now = datetime.now(UTC)
        stmt = pg_insert(TopicReadState).values(
            [
                {
                    "id": uuid.uuid4(),
                    "topic_id": topic_id,
                    "user_handle": user_handle,
                    "last_read_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
                for topic_id in topic_ids
            ]
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[TopicReadState.topic_id, TopicReadState.user_handle],
                set_={"last_read_at": now, "updated_at": now},
            )
        )
        await self._session.flush()

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
    """进度层 storage: one checklist per conversation — a room or a task (#187)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, conversation_id: uuid.UUID) -> TopicProgress | None:
        stmt = select(TopicProgress).where(
            TopicProgress.conversation_id == conversation_id
        )
        return (await self._session.scalars(stmt)).first()

    async def save(
        self,
        conversation_id: uuid.UUID,
        items: list[dict],
        *,
        turn_id: uuid.UUID | None = None,
    ) -> TopicProgress:
        """Overwrite this conversation's checklist (upsert).

        Current state, not history — the conversation timeline is where history
        lives. Callers hand over a fresh list each time; the row is rewritten so
        a reader never sees a half-applied checklist.
        """
        row = await self.get(conversation_id)
        if row is None:
            row = TopicProgress(
                conversation_id=conversation_id, items=[], turn_id=turn_id
            )
            self._session.add(row)
        # Rebind rather than mutate: SQLAlchemy does not track in-place edits of
        # a plain JSON column, so an appended item would silently not be saved.
        row.items = [dict(item) for item in items]
        row.turn_id = turn_id
        await self._session.flush()
        return row
