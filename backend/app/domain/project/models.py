from datetime import datetime
from enum import IntEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Sequence,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

project_seq = Sequence("project_seq")
project_membership_seq = Sequence("project_membership_seq")


class ProjectAiMode(IntEnum):
    """First authz gate for a project's agents (architecture doc §4.3)."""

    OFF = 0  # no agent may act
    ASSISTED = 1  # agents act, human approves per approval_policy
    AUTONOMOUS = 2  # agents act freely within the project's shared permissions


class ProjectApprovalPolicy(IntEnum):
    """How an agent action is approved when ai_mode is ASSISTED."""

    MANUAL = 0  # every action needs explicit human approval
    THRESHOLD = 1  # low-risk actions auto-approved, risky ones prompted
    AUTO = 2  # actions proceed, humans notified


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(
        BigInteger, project_seq, primary_key=True, server_default=project_seq.next_value()
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    color_code: Mapped[str] = mapped_column("color_code", String(7), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True, default="")

    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    leader_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    external_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- 2.0 aggregate-root fields (agent orchestration) ------------------
    # A project is the root topic + git repo + owner of AI mode / approval
    # strategy. These default so every legacy project is well-defined (AI off).
    ai_mode: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0", default=ProjectAiMode.OFF.value
    )
    approval_policy: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default="0",
        default=ProjectApprovalPolicy.MANUAL.value,
    )
    # The project's root thread (project = 根话题). NULL for legacy projects.
    root_thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectMemberRole(IntEnum):
    MEMBER = 0
    ADMIN = 1
    OWNER = 2


class ProjectMembership(Base):
    __tablename__ = "project_membership"

    id: Mapped[int] = mapped_column(
        BigInteger,
        project_membership_seq,
        primary_key=True,
        server_default=project_membership_seq.next_value(),
    )
    project_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("project.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
