"""频道的角标 —— 未读数、通知级别、已读游标。

从 `repositories.py` 拆出来的一段。这几件事共用一套判据（`statements` 里的
`_mentions`、`_muted_now`、`readable_by`）：消息算不算「给我的」、这间房现在静没
静音、这间房我能不能看见。读点在这里写一遍，清单页与通知级别页就不会各答一遍、
各自漂移。

侧栏那盏黄灯（有人回复了我、我还没处理）是另一条查询，住在 `replied.py`。

`TopicRepository` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    and_,
    cast,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import aliased

from app.domain.conversation.models import Conversation
from app.domain.event_author import AuthorType
from app.domain.identity.handles import (
    CHEESE_HANDLE,
    agent_dm_key,
    agent_handle_column,
    agent_instance_handle,
    looks_like_agent_handle,
)
from app.domain.topic.models import (
    NotifyLevel,
    Topic,
    TopicKind,
    TopicMembership,
    TopicReadState,
    private_chat,
)
from app.domain.topic.repositories.statements import (
    KIND_MESSAGE,
    _blocks,
    _mentions,
    _mentions_everyone,
    _muted_now,
    _tasks,
    _threads,
    readable_by,
)
from app.domain.topic.repositories.vocabulary import Unread

if TYPE_CHECKING:
    from app.domain.topic import repositories as pkg


# ---- 话题级未读 (Feishu-style badges) -------------------------------


async def unread_counts(
    repo: pkg.TopicRepository, project_id: uuid.UUID, user_handle: str
) -> dict[uuid.UUID, Unread]:
    """What each conversation of the project has waiting for one person:
    the channels they are in, and the tasks they take part in. One that has
    nothing is left out.

    A message counts once it is after the person's read cursor on its
    conversation (no cursor = all of it) and someone else said it. Only
    kind=message counts — doc edits / events / weeklies have their own
    surfaces.

    A channel's number follows the level the person chose for it
    (`NotifyLevel`), and its main line is bold whenever something new was
    said there, unless it is muted:

    - ``all``: every new message in the main line, and every new reply in
      a 支线 they took part in or were @-ed in.
    - ``mentions`` (default): the main-line messages that @ them or
      @所有人, and the same 支线 replies.
    - ``mute``: only what @s them by name.

    Only people's replies in a 支线 count, the rule its own mark follows
    (`thread.reads`). A private room counts every message.

    A task counts only for the people who take part in it (its owner and
    collaborators), and everything said there by someone else: people and
    AI teammates alike. An AI teammate's message is new content the people
    in the task asked to see, so it counts like a person's. Tool steps are
    not messages and never reach here; the task's own mark still says when
    it is the person's turn. Everyone else follows a task they do not take
    part in from the channel.
    """
    # A private room seats both of its people, so a seat answers for it too.
    joined = or_(
        Topic.kind == TopicKind.root,
        Topic.id.in_(
            select(TopicMembership.topic_id).where(
                TopicMembership.member_handle == user_handle
            )
        ),
    )
    unseen = or_(
        TopicReadState.last_read_seq.is_(None),
        _blocks.c.seq > TopicReadState.last_read_seq,
    )

    def read_state(conversation_id):
        return and_(
            TopicReadState.topic_id == conversation_id,
            TopicReadState.user_handle == user_handle,
        )

    main = (
        select(
            _blocks.c.conversation_id,
            # A private room is two people talking: every message counts.
            func.bool_or(private_chat()),
            func.count(),
            func.count().filter(_mentions(user_handle)),
            func.count().filter(_mentions_everyone()),
        )
        .join(Topic, Topic.id == _blocks.c.conversation_id)
        .outerjoin(TopicReadState, read_state(_blocks.c.conversation_id))
        .where(
            Topic.project_id == project_id,
            readable_by(user_handle),
            joined,
            _blocks.c.kind == KIND_MESSAGE,
            _blocks.c.author != user_handle,
            unseen,
        )
        .group_by(_blocks.c.conversation_id)
    )
    root = _blocks.alias()
    mine = _blocks.alias()
    took_part = or_(
        select(root.c.id)
        .where(root.c.id == _threads.c.root_block_id, root.c.author == user_handle)
        .exists(),
        select(mine.c.id)
        .where(mine.c.conversation_id == _threads.c.id, mine.c.author == user_handle)
        .exists(),
    )
    replies = (
        select(
            _threads.c.room_id,
            func.count().filter(
                or_(
                    and_(
                        took_part,
                        _blocks.c.author_type == AuthorType.participant,
                        ~agent_handle_column(_blocks.c.author),
                    ),
                    _mentions(user_handle),
                )
            ),
            func.count().filter(_mentions(user_handle)),
        )
        .join(_threads, _threads.c.id == _blocks.c.conversation_id)
        .join(Topic, Topic.id == _threads.c.room_id)
        .outerjoin(TopicReadState, read_state(_blocks.c.conversation_id))
        .where(
            Topic.project_id == project_id,
            readable_by(user_handle),
            joined,
            _blocks.c.kind == KIND_MESSAGE,
            _blocks.c.author != user_handle,
            unseen,
        )
        .group_by(_threads.c.room_id)
    )
    tasks = (
        select(_blocks.c.conversation_id, func.count())
        .join(_tasks, _tasks.c.id == _blocks.c.conversation_id)
        .outerjoin(TopicReadState, read_state(_blocks.c.conversation_id))
        .where(
            _tasks.c.project_id == project_id,
            _tasks.c.status == "open",
            or_(
                _tasks.c.owner_handle == user_handle,
                cast(_tasks.c.contributor_handles, JSONB).contains([user_handle]),
            ),
            _blocks.c.kind == KIND_MESSAGE,
            _blocks.c.author != user_handle,
            _blocks.c.author_type == AuthorType.participant,
            unseen,
        )
        .group_by(_blocks.c.conversation_id)
    )
    said: dict[uuid.UUID, tuple[int, int, int]] = {}
    private: set[uuid.UUID] = set()
    for room, two, n, me, everyone in (await repo._session.execute(main)).all():
        said[room] = (int(n), int(me), int(everyone))
        if two:
            private.add(room)
    answered = {
        room: (int(n), int(me))
        for room, n, me in (await repo._session.execute(replies)).all()
    }
    rooms = set(said) | set(answered)
    levels = await _levels(repo, list(rooms), user_handle)
    out: dict[uuid.UUID, Unread] = {}
    for room in rooms:
        n, me, everyone = said.get(room, (0, 0, 0))
        in_threads, me_in_threads = answered.get(room, (0, 0))
        level = levels[room]
        if room in private and level == NotifyLevel.mentions:
            level = NotifyLevel.all
        if level == NotifyLevel.mute:
            unread = Unread(count=me + me_in_threads, new=False, messages=n)
        elif level == NotifyLevel.all:
            unread = Unread(count=n + in_threads, new=n > 0, messages=n)
        else:
            unread = Unread(count=me + everyone + in_threads, new=n > 0, messages=n)
        if unread.count or unread.messages:
            out[room] = unread
    for task, n in (await repo._session.execute(tasks)).all():
        out[task] = Unread(count=int(n), new=True, messages=int(n))
    return out


async def _levels(
    repo: pkg.TopicRepository, topic_ids: list[uuid.UUID], user_handle: str
) -> dict[uuid.UUID, NotifyLevel]:
    """The level each of these channels is at for this person right now: a
    mute that ran out reads as the default."""
    levels = {topic_id: NotifyLevel.mentions for topic_id in topic_ids}
    if not topic_ids:
        return levels
    rows = await repo._session.execute(
        select(
            TopicReadState.topic_id, TopicReadState.notify_level, _muted_now()
        ).where(
            TopicReadState.topic_id.in_(topic_ids),
            TopicReadState.user_handle == user_handle,
        )
    )
    for topic_id, level, muted in rows.all():
        if level == NotifyLevel.mute:
            levels[topic_id] = NotifyLevel.mute if muted else NotifyLevel.mentions
        elif level in NotifyLevel.__members__:
            levels[topic_id] = NotifyLevel(level)
    return levels


async def muted_among(
    repo: pkg.TopicRepository, topic_id: uuid.UUID, handles: list[str]
) -> set[str]:
    """Which of these people have this channel muted right now."""
    if not handles:
        return set()
    return set(
        await repo._session.scalars(
            select(TopicReadState.user_handle).where(
                TopicReadState.topic_id == topic_id,
                TopicReadState.user_handle.in_(handles),
                _muted_now(),
            )
        )
    )


async def private_unread_counts(
    repo: pkg.TopicRepository,
    project_id: uuid.UUID,
    user_handle: str,
    *,
    seats: dict[str, str],
    default_agent_instance_id: uuid.UUID | None,
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

    ``seats`` 是「席位 handle → 队友的名字」，从外面喂进来：答得出这份对应的
    是 `agent_instance`（第 5 层），本包在第 2 层，够不着它的表
    （``app.domain.topic.ports.SeatHandles``）。
    """
    default_handle = CHEESE_HANDLE
    if default_agent_instance_id is not None:
        # 项目自己的芝士也在 `seats` 里（它是这个项目的一个实例），所以这一问
        # 是一次查表，不是第二次读库。
        default_handle = seats.get(
            agent_instance_handle(default_agent_instance_id), CHEESE_HANDLE
        )
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
        .join(_blocks, _blocks.c.conversation_id == Topic.id)
        .outerjoin(
            TopicReadState,
            and_(
                TopicReadState.topic_id == Topic.id,
                TopicReadState.user_handle == user_handle,
            ),
        )
        .where(
            Topic.project_id == project_id,
            private_chat(),
            select(func.count())
            .select_from(TopicMembership)
            .where(TopicMembership.topic_id == Topic.id)
            .scalar_subquery()
            == 2,
            _blocks.c.kind == KIND_MESSAGE,
            # A private room has tasks too — resolving an upstream
            # conflict opens one there — and the same rule applies: the
            # badge is about the room's own conversation, which the join
            # on `conversation_id` already picks.
            _blocks.c.author != user_handle,
            or_(
                TopicReadState.last_read_seq.is_(None),
                _blocks.c.seq > TopicReadState.last_read_seq,
            ),
        )
        .group_by(theirs.member_handle)
    )
    rows = (await repo._session.execute(stmt)).all()
    counts: dict[str, int] = {}
    for other, count in rows:
        if looks_like_agent_handle(other):
            key = agent_dm_key(seats.get(other, default_handle))
        else:
            key = other
        counts[key] = counts.get(key, 0) + int(count)
    return counts


async def notify_levels(
    repo: pkg.TopicRepository, project_id: uuid.UUID, user_handle: str
) -> dict[uuid.UUID, tuple[NotifyLevel, datetime | None]]:
    """The user's channels that are not at the default level right now:
    {topic_id: (level, muted until)}. A mute that ran out is the default
    again and is left out."""
    stmt = (
        select(
            TopicReadState.topic_id,
            TopicReadState.notify_level,
            TopicReadState.muted_until,
        )
        .join(Topic, Topic.id == TopicReadState.topic_id)
        .where(
            Topic.project_id == project_id,
            # 同 unread_counts 的读权限：被请出去的私密房间，我当年的静音记录
            # 还在，但它的 id 不能再告诉我。
            readable_by(user_handle),
            TopicReadState.user_handle == user_handle,
            or_(TopicReadState.notify_level == NotifyLevel.all, _muted_now()),
        )
    )
    rows = (await repo._session.execute(stmt)).all()
    return {topic_id: (NotifyLevel(level), until) for topic_id, level, until in rows}


async def set_notify_level(
    repo: pkg.TopicRepository,
    topic_id: uuid.UUID,
    user_handle: str,
    level: NotifyLevel,
    muted_until: datetime | None = None,
) -> None:
    """Set the user's notification level on a channel (upsert). A channel
    the user never opened gets a cursor at 0 — the same as no cursor, so
    muting a channel does not mark it read."""
    stmt = select(TopicReadState).where(
        TopicReadState.topic_id == topic_id,
        TopicReadState.user_handle == user_handle,
    )
    until = muted_until if level == NotifyLevel.mute else None
    state = (await repo._session.scalars(stmt)).first()
    if state is None:
        repo._session.add(
            TopicReadState(
                topic_id=topic_id,
                user_handle=user_handle,
                notify_level=level.value,
                muted_until=until,
            )
        )
    else:
        state.notify_level = level.value
        state.muted_until = until
    await repo._session.flush()


async def mark_read_many(
    repo: pkg.TopicRepository, topic_ids: list[uuid.UUID], user_handle: str
) -> None:
    """Move the user's read cursor on each of these conversations to its
    newest block, in one statement (「全部标为已读」). Rows that exist keep
    their notify level.

    "Newest" is the largest number committed: a block still being stored
    gets a larger one and stays unread, which is right, since the reader
    could not have seen it."""
    if not topic_ids:
        return
    now = datetime.now(UTC)
    # Each conversation's newest number, asked of it alone: a correlated
    # ``max`` walks ``ix_blocks_conversation_seq`` backwards to one row, where
    # joining every block of the conversation and grouping read all of them
    # (30,000 blocks: 10.7 ms against 0.46 ms), on every opening of a room.
    last_seq = (
        select(func.max(_blocks.c.seq))
        .where(_blocks.c.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )
    newest = select(
        func.gen_random_uuid(),
        Conversation.id,
        literal(user_handle),
        func.coalesce(last_seq, 0),
        literal(now),
        literal(now),
    ).where(Conversation.id.in_(topic_ids))
    stmt = pg_insert(TopicReadState).from_select(
        [
            "id",
            "topic_id",
            "user_handle",
            "last_read_seq",
            "created_at",
            "updated_at",
        ],
        newest,
    )
    await repo._session.execute(
        stmt.on_conflict_do_update(
            index_elements=[TopicReadState.topic_id, TopicReadState.user_handle],
            set_={"last_read_seq": stmt.excluded.last_read_seq, "updated_at": now},
        )
    )
    await repo._session.flush()


async def mark_read(
    repo: pkg.TopicRepository, topic_id: uuid.UUID, user_handle: str
) -> None:
    """Move the user's read cursor on a conversation to its newest block
    (upsert)."""
    await mark_read_many(repo, [topic_id], user_handle)
