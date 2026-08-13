"""Milestone model — spec §7.2 (日历/时间维度), §7.3 (随手记里程碑).

Milestones power the project calendar (deadlines, 中期检查/结题) and bubble up to
the Space board. They can be created by hand or auto-pinned by 芝士 from an
activity/event topic (spec §6.2 记一笔).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class MilestoneStatus(enum.StrEnum):
    upcoming = "upcoming"
    done = "done"
    missed = "missed"


class Milestone(UuidPk, Timestamps, Base):
    __tablename__ = "milestones"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus, native_enum=False, length=16),
        default=MilestoneStatus.upcoming,
    )
    # Where it came from (an event/activity topic), if any.
    source_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    # Whether 芝士 pinned this automatically (vs. set by a person).
    auto_pinned: Mapped[bool] = mapped_column(default=False)
