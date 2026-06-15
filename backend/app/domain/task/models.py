"""Task Template & Task models — spec §4.

Task Template = 一个项目集/活动 ("创研课 2026 秋"), shown as a column on the Space
page. It is a PROTOCOL (spec §4.2): the institution offers a resource pack, the
project accepts conditions. Task = 具体题目 under a template; a team links a Task
to spin up / attach a Project.

resource_pack and conditions are stored as JSON (spec open question #1: we use
structured config). conditions is a list of required-topic/approval rules the
linked project must satisfy (e.g. {"required_topic": "结题答辩",
"reviewer_role": "mentor"}).
"""

import uuid

from sqlalchemy import JSON, ForeignKey, String, Text
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


class Task(UuidPk, Timestamps, Base):
    __tablename__ = "tasks"

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_templates.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
