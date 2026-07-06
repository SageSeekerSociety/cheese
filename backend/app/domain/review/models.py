"""Review (验收): a request to accept a thread's work (architecture doc §4.4, P3).

A review targets a thread (a session = git branch). Acceptance is the record
that the work is approved; the actual采纳=merge action (applying it to the
parent branch) is orchestration/git territory, layered on top later. Isolated
domain — one row per review request, with a single overall decision.
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import BigInteger, DateTime, Sequence, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

review_seq = Sequence("review_seq")


class ReviewStatus(IntEnum):
    PENDING = 0
    ACCEPTED = 1
    REJECTED = 2


class Review(Base):
    __tablename__ = "review"

    id: Mapped[int] = mapped_column(
        BigInteger, review_seq, primary_key=True, server_default=review_seq.next_value()
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    status: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=ReviewStatus.PENDING.value
    )
    requested_by_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decided_by_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
