"""Topic model.

A Topic is a ROOM: a place people talk in. The root topic is the project
itself and its children are the project's rooms; nothing hangs below a room in
this table any more. The work that used to live there is a `tasks` row plus a
thread key on its blocks (`app.domain.room_task`), which is what let it stop
paying a room's costs.

Lifecycle: active → archived, and ONLY a person moves it (#442 decision 1). A
merge no longer archives anything — it stamps `accepted_by`/`accepted_at`
("这一次交付完成了") and leaves the topic active, because a topic is usually
continuous and the next piece of work happens in the same room. Conversation can
still be appended after archive. Where each agent's conversation here got to
lives in `agent_sessions`, one row per agent (spec §9).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

# The registry `conversation_id` points at: mapped wherever this is, so the
# foreign key resolves in a process that never imports `app.models`.
from app.domain.conversation.models import Conversation  # noqa: F401


class TopicStatus(enum.StrEnum):
    active = "active"
    archived = "archived"
    draft = "draft"


class TopicKind(enum.StrEnum):
    """Which kind of ROOM this is. Both hold a roster and outlive the work in
    them; the only difference is that one of them is the project itself.

    ``task``/``subtopic`` were how a piece of work said it was a room, back when
    `blocks.topic_id` was the only key a conversation could be grouped by.
    Migration ``a9f3c7e21b04`` converted every such row into a `tasks` row and
    deleted it, so nothing carries these values any more and nothing writes
    them. They stay in the enum for one reason: a database restored from before
    that migration still contains them, and an enum that cannot read its own
    history turns a restore into a crash.
    """

    root = "root"  # 根话题 = 项目本身, 芝士本体
    topic = "topic"  # 二级话题 = 房间
    task = "task"  # 历史值: 一件活曾经也是一行 topics
    subtopic = "subtopic"  # 历史值: task 的前身


class NotifyLevel(enum.StrEnum):
    """How much of a channel reaches one person (`TopicReadState.notify_level`)."""

    all = "all"  # 所有新消息
    mentions = "mentions"  # 只在 @我和我参与的支线有回复时（默认）
    mute = "mute"  # 静音：只有 @我


def room_ref(room: "Topic") -> dict:
    """A room named inside another listing: its id and title."""
    return {"id": str(room.id), "title": room.title}


class TopicRole(enum.StrEnum):
    """A seat's role in a channel. ``owner`` is the person who created it, and
    manages it alongside whoever manages the project; everyone else is a
    ``member``."""

    owner = "owner"
    member = "member"


class Topic(UuidPk, Timestamps, Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_topics_project_number"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Self-referential tree. NULL parent_id = root topic of the project.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    # Its number among the project's channels, for addresses (`project_counters`).
    # NULL until one is taken; the address falls back to the id meanwhile.
    # A private chat has none: it is not a channel anyone links to.
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What a channel is for, in its managers' words. Shown under its name.
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    kind: Mapped[TopicKind] = mapped_column(
        Enum(TopicKind, native_enum=False, length=16), default=TopicKind.topic
    )
    status: Mapped[TopicStatus] = mapped_column(
        Enum(TopicStatus, native_enum=False, length=16),
        default=TopicStatus.active,
    )
    # A room keeps its script revision when project settings change.
    environment: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The compute choice (`ComputeChoice`) every new agent session in this room
    # starts on. NULL = the project default. Written in full the first time the
    # room runs, so a later session gets what the first one got. It is a default
    # and not a placement: where a conversation actually runs is its own
    # session's business (`agent_sessions.execution_request` / `.work_lease`).
    compute_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 私聊 (spec §1): a 1:1 conversation, not shown in the topic tree; uses the
    # participants' cross-project personal memory (spec §8.4).
    # 谁在这间私聊里，答案只在名册上（`TopicMemberService.private_seats`，结论 19）。
    is_private: Mapped[bool] = mapped_column(default=False, server_default="false")
    # 私密频道: a channel only the people in it see — its name, its tasks, its
    # messages. Nobody joins it; its people bring others in. 综合 is never one.
    # Unlike a private chat it is an ordinary channel in every other way.
    members_only: Mapped[bool] = mapped_column(default=False, server_default="false")
    # If this topic was upgraded from a block (讨论升级 / 拆解), link it back.
    # use_alter: topics↔blocks is a circular FK; add this one via ALTER.
    upgraded_from_block_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "blocks.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_topics_upgraded_from_block_id",
        ),
        nullable=True,
    )

    # 交付标记：合并那一刻自动打上（review/services.py），撤回采纳会清掉。
    # 归档是独立的、人为的动作 —— archived_at 与这两个字段互不牵连。
    accepted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Archived because its project was, not by itself. Unarchiving the project
    # brings back exactly these rooms and leaves the ones a person had already
    # archived where they were.
    archived_with_project: Mapped[bool] = mapped_column(
        default=False, server_default="false"
    )
    cleanup_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    cleanup_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class RoomCleanup(UuidPk, Timestamps, Base):
    __tablename__ = "room_cleanups"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    topic_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    resources: Mapped[list] = mapped_column(JSON, default=list)
    last_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Attempts in a row that failed for ``last_error``: what the next one waits
    # (see retire.py).
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Which sweep is working on this cleanup, until when (see retire.py).
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_holder: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RawTranscript(UuidPk, Timestamps, Base):
    """The index of transcript chunks already uploaded to TRANSCRIPT_S3_BUCKET.

    Retained only as that index, pending a decision on those objects; nothing
    writes or reads it. Transcripts are no longer uploaded (topic/retire.py
    keeps them on the session host instead).
    """

    __tablename__ = "raw_transcripts"

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    source: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    chunks: Mapped[list] = mapped_column(JSON, default=list)


class TopicReadState(UuidPk, Timestamps, Base):
    """Per-user read cursor on a conversation: a room's or a task's.

    One row per (conversation, user); last_read_at is bumped whenever the user
    opens it. Unread = message blocks by OTHERS created after this cursor
    (no row = everything by others is unread). Deliberately a cursor, not a
    per-message read table — cheap to bump, cheap to count against. Which
    conversations count for whom is `TopicRepository.unread_counts`.
    """

    __tablename__ = "topic_read_states"
    __table_args__ = (
        UniqueConstraint("topic_id", "user_handle", name="uq_topic_read_user"),
    )

    # A conversation id (`conversations`): the room's own, or a task's.
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_handle: Mapped[str] = mapped_column(String(64), index=True)
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 这个人对这个频道的通知档位（`NotifyLevel`）：所有新消息、只在 @我和我参与的
    # 支线有回复时（默认）、静音。读的人一律经过 `effective_level`，那里把过了期的
    # 静音当成默认。
    notify_level: Mapped[str] = mapped_column(
        String(16), default="mentions", server_default="mentions"
    )
    # 静音到什么时候；空 = 直到本人取消。只在 `mute` 时有值。
    muted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TopicProgress(UuidPk, Timestamps, Base):
    """进度层: what this topic's work has gotten through, so far (#187).

    芝士's checklist (written with `todo_write`) used to live only in the
    turn's WS stream — it died with the turn, and with the machine. That made
    "换了机器不知道自己做到哪" structurally unavoidable: the room could show the
    code, the decisions and the doc, but never the半成品 in between.

    This row is that missing layer, and it is deliberately NOT memory: memory is
    stable facts injected into every prompt, and a running checklist would both
    bloat it and go stale. One row per conversation — a room or a task —
    overwritten in place: the current state of the work, not its
    history (the timeline already keeps history).

    ``items`` is the checklist as the UI renders it: ``[{"id", "subject",
    "status"}]``, status ∈ pending/in_progress/completed. Written the moment the
    agent calls `todo_write` (``PUT /topics/{id}/progress``), not at turn end —
    a turn that dies mid-flight must not take the progress with it, which is the
    whole point.
    """

    __tablename__ = "topic_progress"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True
    )
    items: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # The turn that last wrote this, for telling "left over from a turn that
    # died" apart from "this turn is still going" without joining the timeline.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class TopicMembership(UuidPk, Timestamps, Base):
    """A member of a topic's group room (话题成员名册, fusion-design §3).

    The roster is the foundation of "话题 = 群聊": who is in the room, their
    role, and — via @all/@here — who a broadcast reaches. 芝士 is a member too,
    identified by the handle `cheese` (P1 turns agents into real user rows;
    here they are still string handles, deliberately no user FK).
    """

    __tablename__ = "topic_memberships"
    __table_args__ = (
        UniqueConstraint("topic_id", "member_handle", name="uq_topic_member"),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    member_handle: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[TopicRole] = mapped_column(
        Enum(TopicRole, native_enum=False, length=16),
        default=TopicRole.member,
    )
