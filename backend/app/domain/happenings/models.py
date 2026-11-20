"""One row per thing that happened that a rule might be waiting for.

Written in the transaction that made it happen (``happenings.happened``) and
taken once by whoever reacts to it, which deletes it. The rows say what
happened in data enough to be matched without reading back into the tables
the thing happened in: a task that went to review names its reporter and
reviewers here.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Happening(Base):
    __tablename__ = "happenings"
    __table_args__ = (Index("ix_happenings_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    #: One of ``happenings.KINDS``.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The person it happened to, when it happened to one person.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE", name="fk_happenings_user_id_user"),
        nullable=True,
    )
    #: The task it happened in, when it happened in one.
    task_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
