"""任务 — one piece of work, living as a thread inside a room.

A room (`topics.kind` root/topic) is a long-lived place: a roster, a living
document, a conversation, and it does not end. A task is one piece of work
inside it that DOES end. Pulling work out of the room table is what lets a task
stop paying a room's costs — a roster, an unread cursor, a notification policy,
an archive decision, a line in the sidebar, and the unanswerable "is this room
still alive?".

What a task owns, and the room does not:

- a single owner (`owner_handle`) — not a roster,
- the 分身 doing it (`subagent_id`),
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

from sqlalchemy import (
    ARRAY,
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
    `b8e2f4a90d33`), so every branch, worktree directory, container
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
    # 快检最后一次说了什么，关于这棵树现在的内容。NOT a gate: nothing reads this
    # to decide anything, and #296 settled that the real CI on the PR is what
    # decides. It exists so a red check is VISIBLE to whoever is about to
    # accept — a check nobody sees is a check nobody runs.
    last_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_detail: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 有东西就有 PR (#718 拍板①): the draft PR this batch is being written into,
    # opened at the batch's FIRST COMMIT rather than when a card is filed. It
    # lives on the tree and not on a card because at that moment there is no
    # card — 一棵树 = 一个分支 = 一个 PR = 一批活, and this is the PR half of
    # that sentence finally being written down.
    #
    # Filing a card ADOPTS this PR instead of opening a second one; the card
    # keeps its own `pr_number` because a card can also acquire a PR without a
    # tree ever having one (a legacy card, or a publish that only succeeded at
    # accept time). What this column buys that GitHub cannot is cheapness: the
    # sweep that looks for batches needing a PR has to answer "does this tree
    # already have one" every tick, and asking GitHub would be one failed POST
    # per open tree per tick, forever.
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 这一批交出去的是哪个 commit —— 合并那一刻记下的事实，之后再也不改。
    #
    # NOT the branch's tip. A branch is mutable: a commit pushed onto it after
    # the batch merged (a stale screen, a hand push) would then read as「已经交付
    # 的内容」 while never having been anywhere near main. A device carrying its
    # next batch over uses this as the base of a three-way merge, so a wrong
    # value here silently re-delivers or drops work.
    delivered_head: Mapped[str | None] = mapped_column(String(64), nullable=True)


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

    - `heavy` is REAL. `cheese check` takes it around the project's quick check,
      which is the platform's own code, so the lane can simply be held there.
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
        Index("uq_room_locks_resource", "room_id", "kind", "resource", unique=True),
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
    # old policy. NULL when the project had no default and nobody named one —
    # then the card must name a reviewer itself.
    reviewer_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Explicit credit declarations; ownership does not prove either contribution.
    reporter_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contributor_handles: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 这条活在哪棵树上干. Many tasks share one tree — 一棵树 = 一个分支 =
    # 一个 PR = 一批活 — so this is what says which batch the work belongs to,
    # and it is the only place a task's files live. NOT NULL: a task with no
    # tree has nowhere to write.
    tree_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_trees.id", ondelete="CASCADE"), index=True
    )
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
