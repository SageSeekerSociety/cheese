import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class ModelBehaviorCheck(UuidPk, Base):
    """One thing an AI teammate was asked to do that the platform can see it
    do or not do, and how it went.

    A row is written when the ask reaches the teammate, so the rows of a kind
    are the denominator of its rate; the ones whose ``outcome`` is
    ``"missed"`` are the errors. The platform cannot tell whether a teammate
    followed an instruction in general, only whether it did the one visible
    thing an instruction asks for (calling a tool), so each kind names such
    a thing (`checks.Kind`).
    """

    __tablename__ = "model_behavior_checks"
    __table_args__ = (
        # One ask, one row: the ask is the delivery that carried it.
        UniqueConstraint("kind", "ask_id", name="uq_model_behavior_checks_kind_ask"),
        Index("ix_model_behavior_checks_kind_asked_at", "kind", "asked_at"),
    )

    kind: Mapped[str] = mapped_column(String(64))
    #: The delivery that carried the ask.
    ask_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    #: The conversation the ask was made in (a task's is the task's id).
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    #: The seat that was asked.
    agent_handle: Mapped[str] = mapped_column(String(64))
    #: The model that seat was set to run when it was asked; None when it
    #: names none and runs the platform default.
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: When the ask reached the teammate.
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: ``done``, ``missed`` (the error), or ``moot`` (the ask stopped
    #: mattering before the teammate could miss it); None while still open.
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
