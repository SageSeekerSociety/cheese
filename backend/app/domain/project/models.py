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


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(
        BigInteger, project_seq, primary_key=True, server_default=project_seq.next_value()
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    color_code: Mapped[str] = mapped_column("color_code", String(7), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True, default="")

    # Nullable for 知是 2.0 independent/personal projects (no team, no fixed schedule);
    # legacy team-scoped projects still set them.
    team_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    leader_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    external_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)

    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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
