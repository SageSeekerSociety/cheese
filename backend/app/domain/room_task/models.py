"""任务 — one piece of work, living as a thread inside a room.

A room (`topics.kind` root/topic) is a long-lived place: a roster, a living
document, a conversation, and it does not end. A task is one piece of work
inside it that DOES end. Pulling work out of the room table is what lets a task
stop paying a room's costs — a roster, an unread cursor, a notification policy,
an archive decision, a line in the sidebar, and the unanswerable "is this room
still alive?".

What a task owns, and the room does not:

- a single owner (`owner_handle`) — not a roster,
- the agent doing it (`agent_instance_id`),
- an isolated workspace, named by `branch_name`,
- one thread of conversation — every `Block` whose `task_id` is this row,
- a delivery: `accepted_by` / `accepted_at`.

The conversation is the load-bearing part. Until now the ONLY way to give a
piece of work its own thread was to give it its own row in `topics`, because
`blocks.topic_id` was the sole grouping key; `blocks.task_id` is the key that
makes a thread cheaper than a room.

Deliberately absent, and not an oversight: **dependencies between tasks**. The
design names them, but nothing writes or reads one today, and an empty join
table is indistinguishable from a live one to whoever reads this next.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class TaskStatus(enum.StrEnum):
    """Whether this piece of work is still going.

    Two values, because two is what is actually observable: work is either being
    done or it is not. A richer vocabulary (blocked / in review / …) would be a
    word nothing writes and nothing acts on — see the module docstring on
    dependencies for the same reasoning.

    Delivery is NOT a status: `accepted_at` records it, and a task can be
    delivered and still open (someone keeps pushing to the same branch) or
    closed with nothing delivered (abandoned). Folding both into one column
    would make those two indistinguishable.
    """

    open = "open"
    closed = "closed"


class Task(UuidPk, Timestamps, Base):
    __tablename__ = "tasks"
    # (room_id, created_at) is the room's task list, and it is read on every
    # room open — the same shape as ix_blocks_topic_id_created_at, for the same
    # reason: this must not degrade into a scan as tasks accumulate.
    __table_args__ = (Index("ix_tasks_room_id_created_at", "room_id", "created_at"),)

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
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # WHICH agent works here. NULL = the project's default, same meaning as on a
    # topic, so a task nobody pinned an agent to follows the project.
    agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_instances.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The git branch this work's isolated worktree sits on.
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

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
