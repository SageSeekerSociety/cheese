"""这几块共用的 SQL 词汇 —— 从 `repositories.py` 拆出来的。

三张裸表（`_tasks`、`_threads`、`_blocks`）是别的领域的行，这一包只取要用的列；一组
判据（`_mentions`、`_mentions_everyone`、`_muted_now`、`_seated`、`readable_by`）
和两个派生表达式（`_last_activity`、`_order_by`）都只写一遍，读、写、角标三边共用，
免得同一句 `WHERE` 在几个模块里各答一次、各自漂移。

`_blocks` 是 2026-10-16 加进来的：`block` 在第 4 层、本包在第 2 层，而未读数、
「回复了我」、房间最后一条这三处读的都是那张表的列。规则和上面两张一样 —— 谁的
账号被依赖，谁就不反向够回来，要用的人把 `WHERE` 用到的列逐字声明出来。**只有读
法走这条路**：写一条块（`BlockRepository.add`）带着轮次记账、句子键和实时投递，
仍然由 `block` 提供、在门口递进来（`topic/ports.py` 的 `PlatformLine`）。

`seen_by` 是对外的那一个，`app.domain.topic.repositories` 的 `__init__.py` 再导出。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
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

from app.domain.topic.models import (
    NotifyLevel,
    Topic,
    TopicMembership,
    TopicReadState,
    not_a_private_chat,
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


# 房间和活的时间线。A bare table: `block` is layer 4, this package is layer 2.
# 只声明这几条判据用到的列（`_last_activity`、`_mentions`、`replied.py`、
# `unread.py`、`latest.py`）。
_blocks = table(
    "blocks",
    column("id", Uuid),
    column("conversation_id", Uuid),
    column("seq", BigInteger),
    column("kind", String(16)),
    column("author", String(128)),
    column("author_type", String(16)),
    column("reply_to", Uuid),
    column("meta", JSON),
    column("refs", JSON),
    column("created_at", DateTime(timezone=True)),
)


# `blocks.kind` / `blocks.author_type` 存的是短字符串（`Enum(native_enum=False)`，
# 成员名就是值）。`author_type` 那一档不在这里：`replied.py` 和 `unread.py` 直接
# import 第 1 层的 `app.domain.event_author` —— 判这一列的判据要求按枚举成员读
# （`tests/unit/test_author_type_two_values.py`），抄成字符串正是它要防的东西，而
# 本包够不到第 4 层的那个枚举。`kind` 没有这条要求，本包也看不见
# `block.models.BlockKind`，这个名字在这里逐字写下来 —— 改一个字，这里和
# `block/models.py` 就各说一份，和上面三张表的列名同一条规矩。
KIND_MESSAGE = "message"


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
    return cast(_blocks.c.refs, JSONB).contains([f"user:{handle}"])


def _mentions_everyone() -> ColumnElement[bool]:
    return or_(
        cast(_blocks.c.refs, JSONB).contains(["user:all"]),
        cast(_blocks.c.refs, JSONB).contains(["user:here"]),
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
        select(func.max(_blocks.c.created_at))
        .where(_blocks.c.conversation_id == Topic.id)
        .correlate(Topic)
        .scalar_subquery()
    )
    # The tasks' maximum is the maximum of each task's own last block, read the
    # same way. Joining blocks to tasks and aggregating instead let the planner
    # hash-join the whole blocks table once per listed topic: 50-125 ms of
    # every `GET /topics` on dev (2026-10-31).
    task_last = (
        select(func.max(_blocks.c.created_at))
        .where(_blocks.c.conversation_id == _tasks.c.id)
        .correlate(_tasks)
        .scalar_subquery()
    )
    in_tasks = (
        select(func.max(task_last))
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
    leave them out, and the badge maps (``readable_by``) admit them by seat.
    """
    public = Topic.members_only.is_(False)
    if handle is None:
        return public
    return or_(public, _seated(handle))


def readable_by(user_handle: str):
    """Rooms a person's badge maps may name: every public channel, and the
    private chats and private channels they still hold a seat in. One clause
    for both the unread map and the notify-level map, so the two can never
    disagree about a room."""
    return or_(
        and_(not_a_private_chat(), Topic.members_only.is_(False)),
        _seated(user_handle),
    )
