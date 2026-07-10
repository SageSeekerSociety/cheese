"""Task Template & Task models — spec §4.

Task Template = 一个项目集/活动 ("创研课 2026 秋"), shown as a column on the Space
page. It is a PROTOCOL (spec §4.2): the institution offers a resource pack, the
project accepts conditions. Task = 具体题目 under a template; a team links a Task
to spin up / attach a Project.

resource_pack and conditions are stored as JSON (spec open question #1: we use
structured config). conditions is a list of required-topic/approval rules the
linked project must satisfy (e.g. {"required_topic": "结题答辩",
"reviewer_role": "mentor"}).

匹配市场 (spec §13 阶段 6): a Space publishes a Template to the market
(published=True); a team applies with one of its Projects (TaskApplication);
the Space accepts → a Task is created under the Template and linked to the
Project (= the team signs the protocol, §4.2).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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


class TaskTemplate(UuidPk, Timestamps, Base):
    __tablename__ = "task_templates"

    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # What the institution provides: compute/credits/env templates/mentoring.
    resource_pack: Mapped[dict] = mapped_column(JSON, default=dict)
    # What the project must honor: list of required-topic / approval rules.
    conditions: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Default expert role for projects under this template (spec §8.2).
    default_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 匹配市场: whether this template is listed on the market (spec §13 阶段 6).
    published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )


class ApplicationStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"


class TaskApplication(UuidPk, Timestamps, Base):
    """A team's 应征 on a published Template (匹配市场, spec §13 阶段 6).

    One application per (template, project) — re-applying is idempotent and
    returns the existing row. Accepting creates a Task under the template and
    a ProjectTaskLink (protocol signed, §4.2); the created task is kept on
    task_id for the audit trail.
    """

    __tablename__ = "task_applications"
    __table_args__ = (
        UniqueConstraint("template_id", "project_id", name="uq_task_application"),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_templates.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The team's self-introduction / why they fit this 题目.
    pitch: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, native_enum=False, length=16),
        default=ApplicationStatus.pending,
    )
    # Who decided (a user handle acting for the Space) and when.
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The Task created on accept (audit trail; SET NULL if the task goes away).
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )


class Task(UuidPk, Timestamps, Base):
    __tablename__ = "tasks"

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_templates.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
