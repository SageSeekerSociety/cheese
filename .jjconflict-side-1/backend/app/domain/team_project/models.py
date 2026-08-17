"""知是 team projects (创研项目) — reference: cheese-backend-nt project domain.

Distinct from the cheesex Project (uuid, git-repo workspace, table
``projects``): these are the product-side projects a TEAM runs, int-keyed,
nested one level via parent_id, served at the root ``/team-projects`` routes.

The tables carry the ``team_`` prefix for the same reason the route does (#370):
telling two unrelated resources apart by singular-vs-plural (``project`` here,
``projects`` there) is the most fragile distinction in this fused schema, and it
reads as a typo to everyone who meets it.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ProjectMemberRole:
    LEADER = "LEADER"
    MEMBER = "MEMBER"
    EXTERNAL = "EXTERNAL"

    _LEVELS = {"LEADER": 2, "MEMBER": 1, "EXTERNAL": 0}

    @classmethod
    def is_valid(cls, role: str) -> bool:
        return role in cls._LEVELS


class Project(Base):
    __tablename__ = "team_project"
    __table_args__ = (
        Index("ix_team_project_team_id", "team_id"),
        Index("ix_team_project_leader_id", "leader_id"),
        Index("ix_team_project_parent_id", "parent_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    color_code: Mapped[str] = mapped_column(String(7), nullable=False)
    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Foreign keys as simple ids (matching the sibling 知是 domains).
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    leader_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    external_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_repo: Mapped[str | None] = mapped_column(String, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectMembership(Base):
    __tablename__ = "team_project_membership"
    __table_args__ = (
        Index("ix_team_project_membership_project_id", "project_id"),
        Index("ix_team_project_membership_user_id", "user_id"),
        Index(
            "uq_team_project_membership_project_user",
            "project_id",
            "user_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
