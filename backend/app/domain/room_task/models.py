"""A task owns one branch, worktree and PR inside a collaboration room."""

import enum
import uuid
from datetime import datetime, timedelta

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class TaskStatus(enum.StrEnum):
    """Whether this piece of work is still going.

    Two values, because two is what is actually observable: work is either being
    done or it is not. A richer vocabulary (blocked / in review / …) would be a
    word nothing writes and nothing acts on — see the module docstring on
    dependencies for the same reasoning.

    Acceptance closes the task, while a task may also close without delivery.
    `accepted_at` records approval and `delivered_head` retains the merged
    revision even if that approval is later revoked.
    """

    open = "open"
    closed = "closed"


class LockKind(enum.StrEnum):
    heavy = "heavy"


HEAVY_LOCK_TTL = timedelta(minutes=30)


class RoomLock(UuidPk, Timestamps, Base):
    """Serialize operations competing for a room's machine resources."""

    __tablename__ = "room_locks"
    __table_args__ = (
        Index("uq_room_locks_resource", "room_id", "kind", "resource", unique=True),
        Index("ix_room_locks_expires_at", "expires_at"),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    kind: Mapped[LockKind] = mapped_column(String(16))
    #: Empty for the room's heavy-operation lock. Empty rather than NULL so
    #: the unique index constrains it — NULL never equals NULL, so a nullable
    #: column would let one room hold any number of heavy locks.
    resource: Mapped[str] = mapped_column(String(1024), default="", server_default="")
    #: NULL = the room's main executor holds the heavy-operation lock.
    holder_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Task(UuidPk, Timestamps, Base):
    __tablename__ = "tasks"
    # (room_id, created_at) is the room's task list, and it is read on every
    # room open — the same shape as ix_blocks_topic_id_created_at, for the same
    # reason: this must not degrade into a scan as tasks accumulate.
    __table_args__ = (
        Index("ix_tasks_room_id_created_at", "room_id", "created_at"),
        # Every hook event a worker produces asks "whose work is this?", so this
        # lookup runs on each tool call in the room — the one index whose
        # absence would be paid per event rather than per page.
        Index("ix_tasks_room_id_subagent_id", "room_id", "subagent_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The room this thread hangs in. Always a room (`kind` root/topic) — work
    # does not nest, so a task's task is another task in the SAME room.
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=16),
        default=TaskStatus.open,
    )
    # 唯一的主: the one member this work belongs to. A room has a roster; a task
    # has an owner, and that difference is the point of the split.
    owner_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 谁来验收这条活 (#718 设置表「任务默认 reviewer」). Distinct from
    # `owner_handle`: the owner is whose work this is, the reviewer is who says
    # it may land, and a project where those are the same person is a project
    # with no review.
    #
    # Resolved and WRITTEN when the work is dispatched (explicit `--reviewer`,
    # else the project's default), rather than read back out of the project
    # setting at 递卡 time. The setting is a policy that can change; who a piece
    # of work was handed to is a fact about the moment it was handed over, and a
    # value re-derived later would silently re-route work dispatched under the
    # old policy. New work requires a reviewer; NULL remains possible on
    # historical records.
    reviewer_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Explicit credit declarations; ownership does not prove either contribution.
    reporter_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contributor_handles: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Historical tasks have no branch of their own. Their original shared
    # delivery is retained as a task, with the original branch and PR.
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    historical_delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    historical_claimed_paths: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )
    historical_delivery: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivered_head: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_detail: Mapped[str] = mapped_column(Text, default="", server_default="")
    # WHICH worker inside the room's session is doing this. A subagent is a
    # second worker in one Claude session: its hooks come up the SAME pipe as
    # the room's own, carrying `agent_id` and nothing else to say whose they
    # are (the room's own events carry no such key at all). So this column is
    # the whole of the attribution — without it every tool call a worker makes
    # reads as the room's, and the room's timeline is one interleaved stream
    # from nobody.
    #
    # A string, not a foreign key: the id is minted by Claude Code inside the
    # container, and the platform only ever recognises it. NULL means nobody
    # has claimed this work yet — a task row exists from the moment it is
    # dispatched, and the worker is bound a moment later, once the room has
    # actually spawned one.
    subagent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 最后一次有人确认这条活还活着。Stamped when a worker is bound; the board
    # reads it together with the thread's last block, and takes the later of the
    # two — a worker that has said nothing yet has only this, and one that has
    # been going for hours has only the blocks. Nothing else writes it, because
    # nothing else knows: the work happens inside a session the platform does
    # not drive.
    last_turn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 简报原文, written once when the work is dispatched and never edited —
    # a brief is a statement of what was asked for, and one that could be
    # rewritten afterwards would stop being evidence of that.
    #
    # It lives on the row rather than in a document of its own because the
    # document had no maintainer: work is a subagent holding the room's token,
    # which cannot reach a thread's doc address at all, so what got seeded at
    # dispatch stayed frozen there forever while the real state moved on.
    brief: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 分身交回来的最后一句话 —— `SubagentStop.last_assistant_message`, written
    # by the platform every time a bound worker hands something back, each one
    # overwriting the last. A worker reports finished more than once (parking a
    # long command counts), so the newest is the only one worth keeping and no
    # single one of them means the work is over. What ends it is the room
    # closing the card, after reading this.
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 交付标记, stamped when the work merges. Independent of `status`, above.
    accepted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the thread was collapsed. `status == closed` is the flag; this is
    # when — kept apart so "closed" needs no timestamp to be true.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # A thread has its own device home, so the same stamp as on a topic: when
    # its raw Claude session files last reached the platform.
    transcripts_archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # If this work was dispatched from a message in the room, the block it came
    # from — so that position in the timeline stays a live link to the thread.
    # use_alter: tasks↔blocks is a circular FK; add this one via ALTER.
    upgraded_from_block_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "blocks.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_tasks_upgraded_from_block_id",
        ),
        nullable=True,
    )
