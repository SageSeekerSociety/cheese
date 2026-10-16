"""这几块共用的 SQL 词汇 —— 从 `repositories.py` 拆出来的。

两张裸表（`_tasks`、`_threads`）是别的领域的行，这一包只取要用的列；一组判据
（`_mentions`、`_mentions_everyone`、`_muted_now`、`_seated`、`_readable_by`）和两个
派生表达式（`_last_activity`、`_order_by`）都只写一遍，读、写、角标三边共用，免得
同一句 `WHERE` 在几个模块里各答一次、各自漂移。

`seen_by` 是对外的那一个，`app.domain.topic.repositories` 的 `__init__.py` 再导出。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    String,
    Uuid,
    and_,
    cast,
    column,
    func,
    or_,
    select,
    table,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import (
    ColumnElement,
    SQLColumnExpression,
    UnaryExpression,
)

from app.domain.block.models import Block
from app.domain.topic.models import (
    NotifyLevel,
    Topic,
    TopicMembership,
    TopicReadState,
)
from app.domain.topic.repositories.vocabulary import SortOrder, TopicSortField

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


# 支线 under a channel. A bare table: `thread` resolves places through
# `room_task`, which depends on this domain.
_threads = table(
    "threads",
    column("id", Uuid),
    column("room_id", Uuid),
    column("root_block_id", Uuid),
)


def _muted_now() -> ColumnElement[bool]:
    """The read-state row says 静音 and the mute has not run out."""
    return and_(
        TopicReadState.notify_level == NotifyLevel.mute,
        or_(
            TopicReadState.muted_until.is_(None),
            TopicReadState.muted_until > func.now(),
        ),
    )


def _mentions(handle: str) -> ColumnElement[bool]:
    return cast(Block.refs, JSONB).contains([f"user:{handle}"])


def _mentions_everyone() -> ColumnElement[bool]:
    return or_(
        cast(Block.refs, JSONB).contains(["user:all"]),
        cast(Block.refs, JSONB).contains(["user:here"]),
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


def _seated(handle: str):
    return Topic.id.in_(
        select(TopicMembership.topic_id).where(TopicMembership.member_handle == handle)
    )


def seen_by(handle: str | None) -> ColumnElement[bool]:
    """Channels a person sees: every public one, and the private channels they
    sit in. ``None`` is nobody in particular, who sees the public ones.

    Private chats are not decided here: the listings that use this already
    leave them out, and the badge maps (``_readable_by``) admit them by seat.
    """
    public = Topic.members_only.is_(False)
    if handle is None:
        return public
    return or_(public, _seated(handle))


def _readable_by(user_handle: str):
    """Rooms a person's badge maps may name: every public channel, and the
    private chats and private channels they still hold a seat in. One clause
    for both the unread map and the notify-level map, so the two can never
    disagree about a room."""
    return or_(
        and_(Topic.is_private.is_(False), Topic.members_only.is_(False)),
        _seated(user_handle),
    )
