import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Sequence,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.domain.task.types import TaskAIAdviceStatus

task_seq = Sequence("task_seq")
task_membership_seq = Sequence("task_membership_seq")
task_topics_relation_seq = Sequence("task_topics_relation_seq")
task_submission_seq = Sequence("task_submission_seq")
task_submission_entry_seq = Sequence("task_submission_entry_seq")
task_submission_review_seq = Sequence("task_submission_review_seq")
task_ai_advice_context_seq = Sequence("task_ai_advice_context_seq")
task_submission_schema_seq = Sequence("task_submission_schema_seq")


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(BigInteger, task_seq, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    intro: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Foreign keys as simple ids for now; detailed relationships can be added later.
    creator_id: Mapped[int] = mapped_column("creator_id", Integer, nullable=False)
    space_id: Mapped[int] = mapped_column("space_id", BigInteger, nullable=False)
    category_id: Mapped[int] = mapped_column("category_id", BigInteger, nullable=False)

    # Business fields relevant for listing/filtering/sorting.
    submitter_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    approved: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    participant_limit: Mapped[int | None] = mapped_column(
        "participant_limit", Integer, nullable=True
    )
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registration_start_at: Mapped[datetime | None] = mapped_column(
        "registration_start_at", DateTime(timezone=True), nullable=True
    )
    default_deadline: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resubmittable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rank: Mapped[int | None] = mapped_column("rank", Integer, nullable=True)
    require_real_name: Mapped[bool] = mapped_column(
        "require_real_name", Boolean, nullable=False, default=False
    )
    min_team_size: Mapped[int | None] = mapped_column("min_team_size", Integer, nullable=True)
    max_team_size: Mapped[int | None] = mapped_column("max_team_size", Integer, nullable=True)
    reject_reason: Mapped[str] = mapped_column("reject_reason", String, nullable=False, default="")
    video_url: Mapped[str | None] = mapped_column("video_url", String, nullable=True)
    team_locking_policy: Mapped[str] = mapped_column(
        "team_locking_policy", String(50), nullable=False, default="NO_LOCK"
    )
    team_id: Mapped[int | None] = mapped_column("team_id", BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskMembership(Base):
    __tablename__ = "task_membership"

    id: Mapped[int] = mapped_column(BigInteger, task_membership_seq, primary_key=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("task.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    participant_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )

    approved: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_team: Mapped[bool] = mapped_column("is_team", Boolean, nullable=False, default=False)

    email: Mapped[str] = mapped_column(String, nullable=False, default="")
    phone: Mapped[str] = mapped_column(String, nullable=False, default="")

    completion_status: Mapped[str] = mapped_column(
        "completion_status", String(50), nullable=False, default="NOT_SUBMITTED"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskSubmissionSchemaEntry(Base):
    """Minimal mapping for task_submission_schema table (ElementCollection in Kotlin)."""

    __tablename__ = "task_submission_schema"

    task_id: Mapped[int] = mapped_column(
        "task_id", BigInteger, ForeignKey("task.id"), primary_key=True
    )
    index: Mapped[int] = mapped_column("index", Integer, primary_key=True)
    description: Mapped[str] = mapped_column("description", String, nullable=False)
    type: Mapped[int] = mapped_column("type", SmallInteger, nullable=False)


class TaskTopicsRelation(Base):
    """Minimal mapping for task_topics_relation used for topic-based filtering."""

    __tablename__ = "task_topics_relation"

    id: Mapped[int] = mapped_column(BigInteger, task_topics_relation_seq, primary_key=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("task.id"), nullable=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskSubmission(Base):
    """Minimal mapping for task_submission table."""

    __tablename__ = "task_submission"

    id: Mapped[int] = mapped_column(BigInteger, task_submission_seq, primary_key=True)
    membership_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_membership.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    submitter_id: Mapped[int] = mapped_column(
        "submitter_id", Integer, nullable=False
    )  # references user.id

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskSubmissionEntry(Base):
    """Minimal mapping for task_submission_entry table."""

    __tablename__ = "task_submission_entry"

    id: Mapped[int] = mapped_column(BigInteger, task_submission_entry_seq, primary_key=True)
    task_submission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_submission.id"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_text: Mapped[str | None] = mapped_column("content_text", Text, nullable=True)
    content_attachment_id: Mapped[int | None] = mapped_column(
        "content_attachment_id",
        Integer,
        nullable=True,
        comment="References attachment.id; kept nullable for pure-text entries.",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskSubmissionReview(Base):
    """Minimal mapping for task_submission_review table."""

    __tablename__ = "task_submission_review"

    id: Mapped[int] = mapped_column(BigInteger, task_submission_review_seq, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_submission.id"), nullable=False
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskAIAdvice(Base):
    __tablename__ = "task_ai_advice"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("task.id"), nullable=False)
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class TaskAIAdviceContext(Base):
    __tablename__ = "task_ai_advice_context"

    id: Mapped[int] = mapped_column(BigInteger, task_ai_advice_context_seq, primary_key=True)
    task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("task.id"), nullable=False)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
