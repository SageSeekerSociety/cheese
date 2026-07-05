"""Milestone: a project-scoped deadline / calendar item (architecture doc §9, P6).

Isolated domain — a due date the project (and its agents) track. ``PROTOCOL``
milestones are the協议必做 ones. Status is an explicit lifecycle; whether a
milestone is overdue is derived from ``due_at`` vs now, not stored.
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import BigInteger, DateTime, Sequence, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

milestone_seq = Sequence("milestone_seq")


class MilestoneKind(IntEnum):
    NORMAL = 0
    PROTOCOL = 1  # 协议必做里程碑


class MilestoneStatus(IntEnum):
    OPEN = 0
    DONE = 1
    CANCELLED = 2


class Milestone(Base):
    __tablename__ = "milestone"

    id: Mapped[int] = mapped_column(
        BigInteger, milestone_seq, primary_key=True, server_default=milestone_seq.next_value()
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=MilestoneKind.NORMAL.value
    )
    status: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=MilestoneStatus.OPEN.value
    )
    created_by_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
