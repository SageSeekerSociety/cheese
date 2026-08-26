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
- one thread of conversation — every `Block` whose `task_id` is this row,
- a delivery: `accepted_by` / `accepted_at`.

What a task does NOT own is a tree. It works in its room's current `WorkTree`,
with its siblings, and that tree is what carries a batch of work into one PR.

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
from datetime import datetime, timedelta

from sqlalchemy import ARRAY, DateTime, Enum, ForeignKey, Index, String, text
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


class TreeStatus(enum.StrEnum):
    """Whether this tree still takes work, and whether it has landed.

    `open` — new tasks join it, everyone writes to it.
    `sealed` — its PR is in flight. Nothing new joins, and the tasks already on
      it finish the turn they are in and stop; the tree IS the PR's content, so
      a write here would move the PR out from under the CI run checking it.
    `merged` — the PR landed. Kept rather than deleted because the tasks that
      produced it still point here, and a task whose tree vanished could not say
      where its work went.

    A room seals one tree and opens the next, which is what lets it keep working
    while a PR flies. That is the whole reason a room holds more than one.
    """

    open = "open"
    sealed = "sealed"
    merged = "merged"


class Residency(enum.StrEnum):
    """Whether this task is using one of its room's slots right now.

    `running` — a turn is going, or one is queued to start.
    `idle` — quiet; the slot is released.

    `idle` is NOT "finished". A task goes idle at the end of a turn and comes
    back the moment anyone speaks to it, with its conversation and its files
    untouched. That is what makes releasing the slot free — and therefore
    automatic, rather than something a person has to remember to do.

    DeepSeek Harness has a third value here, `waiting`: quiet, but still owning
    children that have not finished. It is not reachable for us — 活不嵌套, a
    task's split is a SIBLING in the same room — so it would be a word nothing
    writes and nothing reads, which is the thing `TaskStatus` above refuses for
    the same reason.

    Deliberately separate from `TaskStatus`: open/closed answers "is this work
    still wanted", which is a judgement; residency answers "is it using a slot",
    which is observable. A room whose four slots were held by open-but-idle
    threads could never take new work again, and nothing on screen would say
    why — folding the two together is how that gets built.
    """

    running = "running"
    idle = "idle"


#: 一个房间最多同时开几条后台子代理。The room's own line is NOT one of them, so
#: a busy room runs five agents: four threads and itself. Matching DeepSeek
#: Harness's `maxBackgroundAgents` default, which is also a per-session budget
#: covering every continuable direct child.
MAX_RESIDENT_TASKS_PER_ROOM = 4


class WorkTree(UuidPk, Timestamps, Base):
    """一棵树 = 一个分支 = 一个 PR = 一批活.

    A room's unit of DELIVERY, as distinct from `Task`, which is its unit of
    WORK. The two used to be the same thing — every task had its own branch and
    its commits were merged back into the room's one branch when its conclusion
    was accepted — and that cost both of the things this table buys:

    - the room could not take new work while its PR was in flight, because its
      one tree was the PR's content;
    - a finished task could not deliver on its own schedule, because the merge
      had to wait for whatever the room's branch was doing.

    Many tasks share one tree. That is the normal case: a batch of work goes out
    together and lands together, in one PR, reviewed once.

    The first tree of each room carries the ROOM's id (migration
    `b8e2f4a90d33`), so every branch, worktree directory, jj workspace, container
    workdir and tmux session keeps the name it already had.
    """

    __tablename__ = "work_trees"
    __table_args__ = (
        Index("ix_work_trees_room_id", "room_id"),
        # 一个房间同时只有一棵开着的树: "which tree does this new task join" has
        # to have exactly one answer, and it has to be true before a task can
        # exist — so the database says it, not a convention.
        Index(
            "uq_work_trees_one_open_per_room",
            "room_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    status: Mapped[TreeStatus] = mapped_column(
        String(16), default=TreeStatus.open, server_default="open"
    )
    # When the PR opened, and when it landed. Kept apart from `status` for the
    # same reason `Task` keeps delivery out of its status: "sealed" needs no
    # timestamp to be true, and a tree can be sealed for a long time.
    sealed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LockKind(enum.StrEnum):
    """What a room lock is protecting.

    `file` — one whole-file overwrite. An `Edit` needs no lock: it matches the
      text it means to replace and fails loudly when someone else moved it,
      which IS a compare-and-swap. A `Write` or a shell `>` has no such check,
      so it is the one operation that overwrites in silence.
    `heavy` — the room's single lane for what fights over the machine rather
      than over the tree: a test run, a dependency install, a dev server's port.
      One lane per room, so `resource` is empty for these.
    """

    file = "file"
    heavy = "heavy"


#: How long a lock is honoured before the sweep takes it back. An agent that
#: dies mid-write never returns to unlock, and a lock nobody can release is
#: worse than the overwrite it was preventing.
FILE_LOCK_TTL = timedelta(seconds=60)
#: Longer, because what it guards is longer: a test run or a `uv sync`, not a
#: single write.
HEAVY_LOCK_TTL = timedelta(minutes=30)


class RoomLock(UuidPk, Timestamps, Base):
    """一次只有一个人整块覆盖同一个文件，一次只有一个人跑重活。

    Deliberately narrow, and the two kinds are enforced differently — which is
    worth knowing before trusting either:

    - `heavy` is REAL. Test runs, dependency installs and dev servers go through
      `cheese await`, which is the platform's own code, so the lane can simply
      be held there.
    - `file` is ADVISORY. An agent's `Write` is its harness's tool, not ours; we
      cannot stand in front of it. What this offers is a way for an agent about
      to overwrite a whole file to find out that somebody else is already doing
      it, and `SKILL.md` is what asks it to look.

    Neither makes concurrent writing safe in general — a lock around a write
    cannot prevent a lost update, because the read the write is based on
    happened before the lock existed. `Edit` is what covers that case, by
    matching the text it expects and failing when someone else moved it.
    """

    __tablename__ = "room_locks"
    __table_args__ = (
        Index(
            "uq_room_locks_resource", "room_id", "kind", "resource", unique=True
        ),
        Index("ix_room_locks_expires_at", "expires_at"),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    kind: Mapped[LockKind] = mapped_column(String(16))
    #: The path, for a `file` lock; empty for `heavy`. Empty rather than NULL so
    #: the unique index constrains it — NULL never equals NULL, so a nullable
    #: column would let one room hold any number of heavy locks.
    resource: Mapped[str] = mapped_column(String(1024), default="", server_default="")
    #: NULL = the room's own line holds it. The room writes to the same tree as
    #: its threads, so it is not exempt from either lock.
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
    # 这条活在哪棵树上干. Many tasks share one tree — 一棵树 = 一个分支 =
    # 一个 PR = 一批活 — so this is what says which batch the work belongs to,
    # and it is the only place a task's files live. NOT NULL: a task with no
    # tree has nowhere to write.
    tree_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_trees.id", ondelete="CASCADE"), index=True
    )

    # 占不占本房间的一个额度，见 `Residency`. Materialised rather than derived:
    # dsh can recompute it per call because its children live in the process
    # that asks; ours outlive the backend that started them, so the answer has
    # to survive a restart. `last_turn_at` is what lets a crash be told from a
    # turn that is genuinely still going — a `running` row older than the
    # timeout is a ghost holding a slot, and the sweep releases it.
    residency: Mapped[Residency] = mapped_column(
        String(16), default=Residency.idle, server_default="idle", index=True
    )
    last_turn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 排队中: dispatched, but the room was at its cap. Not a refusal — the
    # condition clears on its own, and a refusal would make the dispatcher
    # decide what to do about it. NULL once it has started.
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 这条活说它要碰哪些路径。Mutable on purpose: a brief is written once and
    # cannot be changed, but a claim always grows — work reaches a file nobody
    # predicted. Overlapping DIRECTORIES are a warning (two threads in
    # `domain/review/` is ordinary); the same FILE is a refusal.
    claimed_paths: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default="{}"
    )

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
