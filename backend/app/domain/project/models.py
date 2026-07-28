"""Project models — spec §4.1, §4.4, §6.

A Project = 根话题 = one git repo. Fully independent: it owns its AI mode,
approval rules, and policies. It may link one or more Tasks to draw on a Task
Template's resource pack (and accept its conditions); an unlinked project is
fully self-governing.
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AiMode(enum.StrEnum):
    # AI 全权负责: 开话题/干活/merge, 人可审计但不阻塞 (spec §4.4)
    autonomous = "autonomous"
    # AI 干活, 人验收 (教育红线: AI 不能验收自己做的东西)
    collaborative = "collaborative"


class Project(UuidPk, Timestamps, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200))
    owner_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # fusion P4: the 知是 Team this project is the AI workspace for (nullable — a
    # personal project has none). Lets a team page open its Project natively.
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("team.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ai_mode: Mapped[AiMode] = mapped_column(
        Enum(AiMode, native_enum=False, length=16),
        default=AiMode.collaborative,
    )
    # The root topic of this project (its 总览/大本营). Set after creation.
    # use_alter: projects↔topics is a circular FK; add this one via ALTER.
    root_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "topics.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_projects_root_topic_id",
        ),
        nullable=True,
    )
    # The 赛题 this project was created from (main's int `task` table). The 1.0
    # team-project already carries this idea as `external_task_id`; a 2.0 project
    # needs it too, or "create a project from this 赛题" produces something with
    # no way back to the 赛题 it came from. Nullable: a project made from the
    # rail belongs to no 赛题.
    external_task_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    # Active expert role name (spec §8.2).
    expert_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 一页纸总结 (spec §7.3/F2): AI-maintained one-pager, 老师 30 秒读懂。
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Free-form policy: branch protection approvals, notify level, etc.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # When the 本体 last ran a heartbeat — used to schedule ≤1 patrol/day/project.
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectTaskLink(UuidPk, Timestamps, Base):
    """Links a Project to a Task (= accepting the Template's protocol)."""

    __tablename__ = "project_task_links"
    __table_args__ = (
        UniqueConstraint("project_id", "task_id", name="uq_project_task"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )


class ProjectRole(enum.StrEnum):
    lead = "lead"  # 组长
    member = "member"
    mentor = "mentor"  # 导师


class ProjectMember(UuidPk, Timestamps, Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_handle", name="uq_project_member"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_handle: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[ProjectRole] = mapped_column(
        Enum(ProjectRole, native_enum=False, length=16),
        default=ProjectRole.member,
    )
