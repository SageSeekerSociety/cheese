"""Topic model.

A Topic = one session = one git branch (spec §6). Topics form a tree via
parent_id: the root topic is the project itself, children are work threads,
grandchildren are sub-tasks where 芝士's 分身 works.

Lifecycle (spec §6.3): active → (Accept = merge) → archived. Conversation can
still be appended after archive. session_id holds the resumable Claude Agent
SDK session (spec §9).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class TopicStatus(enum.StrEnum):
    active = "active"
    archived = "archived"
    draft = "draft"


class TopicKind(enum.StrEnum):
    root = "root"  # 根话题 = 项目本身, 芝士本体
    topic = "topic"  # 二级话题
    subtopic = "subtopic"  # 三级+, 芝士分身工作处


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
    # Claude Agent SDK session id, captured after the first turn; used to resume.
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # git branch backing this topic (spec §6.3); sub-topics branch from parent.
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 私聊 (spec §1): a member's 1:1 with 芝士. Not shown in the topic tree;
    # uses the owner's cross-project personal memory (spec §8.4).
    is_private: Mapped[bool] = mapped_column(default=False, server_default="false")
    private_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
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

    # Accept / archive (spec §6.3).
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
