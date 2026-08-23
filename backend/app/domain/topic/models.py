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
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


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


class TopicRole(enum.StrEnum):
    """A member's role in a topic's roster (话题成员名册, fusion-design §3).

    Distinct from ProjectRole (lead/member/mentor): a topic is a group room and
    its membership governs who can manage the roster and who @all/@here reaches.
    """

    owner = "owner"  # 话题创建者, 不可被移除到只剩空 owner
    admin = "admin"
    member = "member"


class Topic(UuidPk, Timestamps, Base):
    __tablename__ = "topics"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Self-referential tree. NULL parent_id = root topic of the project.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[TopicKind] = mapped_column(
        Enum(TopicKind, native_enum=False, length=16), default=TopicKind.topic
    )
    status: Mapped[TopicStatus] = mapped_column(
        Enum(TopicStatus, native_enum=False, length=16),
        default=TopicStatus.active,
    )
    # WHICH agent works here. NULL = the project's default, so a topic nobody
    # chose an agent for still resolves without carrying a copy of the default
    # around (and follows the project when the default changes).
    #
    # Changing it destroys nothing: a conversation is keyed by (topic, agent) in
    # `agent_sessions`, so the new agent looks up a key with no row and starts
    # fresh while the old one's row stays where it is.
    agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_instances.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Compute pool this topic's turns run on (execution-architecture v4 会话级选择).
    # NULL = project sticky, then the owning team's default. Switchable only
    # until the topic has run — i.e. until it has an `agent_sessions` row — after
    # which it is frozen, matching the device-affinity boundary.
    compute_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # git branch backing this room. Nothing in the app has ever written it —
    # the branch a workspace actually sits on is derived (`branch_for_place`) —
    # so every row is NULL and `cheese status` reported `branch: null` for the
    # whole life of the column. A task's `branch_name` IS written, at creation;
    # this one is the shape that column was copied from, kept because a room's
    # branch may one day want naming and nothing depends on it being absent.
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 私聊 (spec §1): a 1:1 conversation, not shown in the topic tree; uses the
    # participants' cross-project personal memory (spec §8.4).
    #  - 芝士 DM:  private_peer is NULL, private_owner = the member's handle.
    #  - peer DM: two humans; the unordered handle pair is canonicalized so
    #    private_owner = min(a, b), private_peer = max(a, b) — one row, both see it.
    is_private: Mapped[bool] = mapped_column(default=False, server_default="false")
    private_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    private_peer: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


class TopicReadState(UuidPk, Timestamps, Base):
    """Per-user read cursor on a topic (话题级未读, Feishu-style).

    One row per (topic, user); last_read_at is bumped whenever the user opens
    the topic. Unread = message blocks by OTHERS created after this cursor
    (no row = everything by others is unread). Deliberately a cursor, not a
    per-message read table — cheap to bump, cheap to count against.
    """

    __tablename__ = "topic_read_states"
    __table_args__ = (
        UniqueConstraint("topic_id", "user_handle", name="uq_topic_read_user"),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    user_handle: Mapped[str] = mapped_column(String(64), index=True)
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TopicProgress(UuidPk, Timestamps, Base):
    """进度层: what this topic's work has gotten through, so far (#187).

    芝士's checklist (the Task tools' working log) used to live only in the
    turn's WS stream — it died with the turn, and with the machine. That made
    "换了机器不知道自己做到哪" structurally unavoidable: the room could show the
    code, the decisions and the doc, but never the半成品 in between.

    This row is that missing layer, and it is deliberately NOT memory: memory is
    stable facts injected into every prompt, and a running checklist would both
    bloat it and go stale. One row per PLACE — a room's own main line, or one
    thread in it — overwritten in place: the current state of the work, not its
    history (the timeline already keeps history).

    ``items`` is the checklist as the UI renders it: ``[{"id", "subject",
    "status"}]``, status ∈ pending/in_progress/completed. Written the moment a
    Task tool call streams in, exactly like 现场 events (chat.py) — a turn that
    dies mid-flight must not take the progress with it, which is the whole point.
    """

    __tablename__ = "topic_progress"
    # `topic_id` used to BE the primary key. It cannot be any more: a thread's
    # row is identified by (room, thread), and a primary key cannot hold the
    # NULL that says "the room's own main line". The pair of partial unique
    # indexes says what the old primary key said, once per half — one wider
    # index over (topic_id, task_id) would not, because NULL is not equal to
    # NULL in a unique index and every room row would stop being exclusive.
    __table_args__ = (
        Index(
            "uq_topic_progress_room",
            "topic_id",
            unique=True,
            postgresql_where=text("task_id IS NULL"),
        ),
        Index(
            "uq_topic_progress_thread",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
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
