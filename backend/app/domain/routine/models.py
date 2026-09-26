"""Standing work a room's AI teammate runs on a clock or on a project event.

A routine is a rule; a run is one time the rule fired. The run row is written
before any work is dispatched and is unique per (routine, occurrence), so a
second sweep, a second backend process or a restart finds the same row instead
of dispatching the same scheduled moment or event twice.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class RoutineTrigger(enum.StrEnum):
    schedule = "schedule"
    library_file_added = "library_file_added"
    task_closed = "task_closed"
    card_accepted = "card_accepted"


class RoutineState(enum.StrEnum):
    #: Proposed by an AI teammate; runs nothing until a person confirms it.
    draft = "draft"
    active = "active"
    paused = "paused"


class RunStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.succeeded, RunStatus.failed, RunStatus.skipped}
)


class Routine(UuidPk, Timestamps, Base):
    __tablename__ = "routines"
    __table_args__ = (
        Index(
            "ix_routines_due",
            "next_run_at",
            postgresql_where=text("state = 'active' AND next_run_at IS NOT NULL"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    #: The room the work runs in and where its results and notices land.
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    instructions: Mapped[str] = mapped_column(Text)
    #: Which material the work may draw on, in the words the person confirmed.
    context_scope: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Room-relative folder the results are saved under.
    output_dir: Mapped[str] = mapped_column(String(300), default="", server_default="")
    trigger: Mapped[str] = mapped_column(String(32))
    #: schedule: {"freq": hourly|daily|weekly|monthly, "time": "HH:MM",
    #: "minute": int, "weekdays": [0..6], "day": 1..31}.
    #: Events: {"scope": room|project}.
    spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    state: Mapped[str] = mapped_column(String(16), default=RoutineState.draft.value)
    #: The AI teammate seat that does the work.
    agent_handle: Mapped[str] = mapped_column(String(64))
    #: Who asked for it; the person notified of every result.
    owner_handle: Mapped[str] = mapped_column(String(64))
    proposed_by: Mapped[str] = mapped_column(String(64))
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Events that happened before this moment are never this rule's business.
    event_cursor: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Bumped on every edit; a run records the revision it was dispatched with.
    revision: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")


class RoutineRun(UuidPk, Base):
    __tablename__ = "routine_runs"
    __table_args__ = (
        UniqueConstraint("routine_id", "occurrence_key", name="uq_routine_occurrence"),
        Index(
            "ix_routine_runs_open",
            "created_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    routine_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("routines.id", ondelete="CASCADE"), index=True
    )
    #: schedule: the planned instant (ISO, UTC); events: "<kind>:<id>".
    occurrence_key: Mapped[str] = mapped_column(String(300))
    trigger_detail: Mapped[str] = mapped_column(Text, default="", server_default="")
    routine_revision: Mapped[int] = mapped_column(BigInteger, default=1)
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.queued.value)
    #: The delivery ledger event that carries the prompt; its attempt id is the turn.
    delivery_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    outputs: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
