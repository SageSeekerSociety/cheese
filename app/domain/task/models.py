from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.task.types import TaskAIAdviceStatus


class Base(DeclarativeBase):
    """Base declarative for task domain."""


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    intro: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Foreign keys as simple ids for now; detailed relationships can be added later.
    creator_id: Mapped[int] = mapped_column("creator_id", Integer, nullable=False)
    space_id: Mapped[int] = mapped_column("space_id", ForeignKey("space.id"), nullable=False)
    category_id: Mapped[int] = mapped_column(
        "category_id", ForeignKey("space_categories.id"), nullable=False
    )

    # Business fields relevant for listing/filtering/sorting.
    registration_start_at: Mapped[datetime | None] = mapped_column(
        "registration_start_at", nullable=True
    )
    registration_deadline: Mapped[datetime | None] = mapped_column(
        "registration_deadline", nullable=True
    )
    auto_reject_when_full: Mapped[bool] = mapped_column(
        "auto_reject_when_full", Boolean, nullable=False, default=False
    )
    rank_reward: Mapped[int] = mapped_column(
        "rank_reward", Integer, nullable=False, default=1,
        comment="Rank points awarded when submission is accepted",
    )
    submitter_type: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="TaskSubmitterType ordinal: 0=USER,1=TEAM",
    )
    approved: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="ApproveType ordinal: 0=APPROVED,1=DISAPPROVED,2=NONE",
    )
    participant_limit: Mapped[int | None] = mapped_column("participant_limit", Integer, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(nullable=True)
    default_deadline: Mapped[int] = mapped_column(Integer, nullable=False)
    resubmittable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rank: Mapped[int | None] = mapped_column("RANK", Integer, nullable=True)
    require_real_name: Mapped[bool] = mapped_column(
        "require_real_name", Boolean, nullable=False, default=False
    )
    min_team_size: Mapped[int | None] = mapped_column("min_team_size", Integer, nullable=True)
    max_team_size: Mapped[int | None] = mapped_column("max_team_size", Integer, nullable=True)
    reject_reason: Mapped[str] = mapped_column("reject_reason", String, nullable=False, default="")
    team_locking_policy: Mapped[str] = mapped_column(
        "team_locking_policy", String(50), nullable=False, default="NO_LOCK"
    )

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TaskMembership(Base):
    __tablename__ = "task_membership"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("task.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(Integer, nullable=False)

    approved: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_team: Mapped[bool] = mapped_column("is_team", Boolean, nullable=False, default=False)

    email: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)

    completion_status: Mapped[str] = mapped_column(
        "completion_status", String(50), nullable=False, default="NOT_SUBMITTED"
    )

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TaskTopicsRelation(Base):
    """Minimal mapping for task_topics_relation used for topic-based filtering."""

    __tablename__ = "task_topics_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("task.id"), nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TaskSubmission(Base):
    """Minimal mapping for task_submission table."""

    __tablename__ = "task_submission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    membership_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task_membership.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    submitter_id: Mapped[int] = mapped_column(
        "submitter_id", Integer, nullable=False
    )  # references user.id

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TaskSubmissionEntry(Base):
    """Minimal mapping for task_submission_entry table."""

    __tablename__ = "task_submission_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task_submission.id"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_text: Mapped[str | None] = mapped_column("content_text", Text, nullable=True)
    content_attachment_id: Mapped[int | None] = mapped_column(
        "content_attachment_id",
        Integer,
        nullable=True,
        comment="References attachment.id; kept nullable for pure-text entries.",
    )

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TaskSubmissionReview(Base):
    """Minimal mapping for task_submission_review table."""

    __tablename__ = "task_submission_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task_submission.id"), nullable=False
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TaskAIAdvice(Base):
    __tablename__ = "task_ai_advice"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("task.id"), nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TaskAIAdviceStatus.PENDING.value
    )
    topic_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_fields: Mapped[str | None] = mapped_column(Text, nullable=True)
    learning_paths: Mapped[str | None] = mapped_column(Text, nullable=True)
    methodology: Mapped[str | None] = mapped_column(Text, nullable=True)
    team_tips: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TaskAIAdviceContext(Base):
    __tablename__ = "task_ai_advice_context"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("task.id"), nullable=False)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AIConversation(Base):
    __tablename__ = "ai_conversation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    module_type: Mapped[str] = mapped_column(String(64), nullable=False)
    context_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False, default="standard")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AIMessage(Base):
    __tablename__ = "ai_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_conversation.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False, default="standard")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seu_consumed: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
