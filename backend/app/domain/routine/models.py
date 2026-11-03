"""Standing work a room's AI teammate runs on a clock or on a project event.

A routine is a rule; a run is one time the rule fired. The run row is written
before any work is dispatched and is unique per (routine, occurrence), so a
second sweep, a second backend process or a restart finds the same row instead
of dispatching the same scheduled moment or event twice.

A template is neither: it is a filled-in form kept to fill the next one from,
and it belongs to the project rather than to the room or to the person.
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
    #: Saved and running. Nothing waits on a person: an AI teammate creating a
    #: rule and a person creating one both leave it here.
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


class RoutineTemplate(UuidPk, Timestamps, Base):
    """「存成模板」收下来的那一张表：一个项目一份，项目里的人共用。

    它不是规则：没有谁在跑、没有执行记录，删掉也不影响已经建出来的规则。所以它只有
    填表要的那几格，没有 ``owner_handle`` / ``agent_handle`` / ``state`` 这些只有规则
    才有的字段 —— 挑一条模板只是把这几格铺进表里，人还得看一眼再存。

    跟项目走，不跟人走。它此前存在各自的浏览器里（一个项目一个键），换台机器就没了；
    2026-10-15 lz123y 定下搬进项目：同一个项目里的人看到的是同一份，换机器也还在。

    不记谁存的，也没有 ``can_delete``：模板是项目共用的一份起点，删它不改变任何人在跑
    的东西，所以权限不挂在这张表上 —— 读得到这个项目就读得到、也删得掉。
    """

    __tablename__ = "routine_templates"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_routine_templates_project_id",
        ),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200))
    #: 「目标」 — the words a rule's ``instructions`` holds, under the name the
    #: form preset goes by on the screen (``RoutineTemplate.goal``).
    goal: Mapped[str] = mapped_column(Text)
    #: 「补充说明」, empty when the form's was.
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    trigger: Mapped[str] = mapped_column(String(32))
    #: Same shape a rule's is: a schedule's fields, or an event's ``scope``.
    spec: Mapped[dict] = mapped_column(JSONB, default=dict)


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
    #: 「目标」: what the rule is for, in the person's own words. The one field
    #: that has to be filled in.
    instructions: Mapped[str] = mapped_column(Text)
    #: 「补充说明」: anything else the teammate should know. A new name on the
    #: column that held 「使用哪些资料」, not a rename — the release still serving
    #: reads that name. What a rule used to keep in that column and in
    #: ``output_dir`` was folded in here by migration ``3e8b1d5a72c4``.
    note: Mapped[str] = mapped_column(
        "context_scope", Text, default="", server_default=""
    )
    # No ``output_dir``: where the results go is the teammate's call each time it
    # runs, so nothing asks for it any more.
    trigger: Mapped[str] = mapped_column(String(32))
    #: schedule: {"freq": hourly|daily|weekly|monthly, "time": "HH:MM",
    #: "minute": int, "weekdays": [0..6], "day": 1..31}.
    #: Events: {"scope": room|project}.
    spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    state: Mapped[str] = mapped_column(String(16), default=RoutineState.active.value)
    #: The AI teammate seat that does the work.
    agent_handle: Mapped[str] = mapped_column(String(64))
    #: Who asked for it; the person notified of every result.
    owner_handle: Mapped[str] = mapped_column(String(64))
    #: Who created it — a person's handle, or the handle of the teammate that
    #: created it on someone's behalf. The column still carries its birth name:
    #: a rename is a new name on the old column, so it is still `proposed_by`.
    created_by: Mapped[str] = mapped_column("proposed_by", String(64))
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
        UniqueConstraint("message_id", name="uq_routine_runs_message_id"),
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
    #: The run's message in the channel's main line: the teammate's line that
    #: says how the run went, and whose 支线 the run happened in. None for a
    #: run that never got as far as a message (skipped).
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blocks.id", ondelete="SET NULL", name="fk_routine_runs_message_id"),
        nullable=True,
    )
    #: The delivery ledger event that carries the prompt; its attempt id is the turn.
    delivery_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    outputs: Mapped[list] = mapped_column(JSONB, default=list)
    #: The feedback this run handed to its teammate (a list of uuid strings),
    #: for a rule whose spec carries `feedback_batch`; empty otherwise. The
    #: triage selection keeps these out of another batch while this run is
    #: going, and afterwards puts them behind the reports never handed out
    #: (see `routine/service/firing.py`, `feedback/triage.py`).
    feedback_offered: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
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
