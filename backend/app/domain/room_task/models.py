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
    UniqueConstraint,
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

    A task may deliver in several steps: accepting the step its card calls the
    last closes the task, accepting an earlier one leaves it open
    (`review/task_landing.py`). A task may also close without delivery.
    `accepted_at` records the latest approval and `delivered_head` the latest
    merged revision, kept even if that approval is later revoked.
    """

    open = "open"
    closed = "closed"


#: What an unnamed task is called until it is named. The stored text is for
#: agents; each screen renders its reader's own word, which is why
#: ``title_source`` and not the text says the task is unnamed.
PLACEHOLDER_TITLE = "新任务"


class TaskTitleSource(enum.StrEnum):
    """Who decided a task's current title, and so whether the platform may
    still change it (app/domain/room_task/naming.py).

    ``human`` is final: a person typed it. Nothing writes over it after that
    but another person."""

    placeholder = "placeholder"  # still 「新任务」
    auto = "auto"  # the platform named it, or the AI teammate that proposed it
    human = "human"


class TaskTitle(UuidPk, Base):
    """Every title a task has had, newest last, and who gave it.

    Read to see how often automatic names get overridden by people."""

    __tablename__ = "task_titles"
    __table_args__ = (Index("ix_task_titles_task_created", "task_id", "created_at"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[TaskTitleSource] = mapped_column(
        Enum(TaskTitleSource, native_enum=False, length=16)
    )
    # name | calibrate | follow | teammate | rename
    reason: Mapped[str] = mapped_column(String(16))
    by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TaskSnapshot(UuidPk, Timestamps, Base):
    """An immutable backup of uncommitted work, separate from the review branch."""

    __tablename__ = "task_snapshots"
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    head_sha: Mapped[str] = mapped_column(String(64))
    snapshot_sha: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(1024))


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
        UniqueConstraint("project_id", "number", name="uq_tasks_project_number"),
        # The tasks under each message of a channel's main line.
        Index("ix_tasks_upgraded_from_block_id", "upgraded_from_block_id"),
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
    # Its number among the project's tasks, for addresses (`project_counters`).
    # NULL until one is taken; the address falls back to the id meanwhile.
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Title bookkeeping for the platform's naming (app/domain/room_task/naming.py).
    # `title_version` moves on every rename, by anyone: an automatic rename is
    # written only if it still matches the version it was computed from, so a
    # person who renames mid-generation always wins.
    title_source: Mapped[TaskTitleSource] = mapped_column(
        Enum(TaskTitleSource, native_enum=False, length=16),
        default=TaskTitleSource.human,
        server_default=TaskTitleSource.human.value,
    )
    title_version: Mapped[int] = mapped_column(default=0, server_default="0")
    # When the platform last judged this title (named it, or decided to keep
    # it); later messages are what a follow-up judgement reads.
    title_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The title has been re-read against the first turn's conversation, not
    # just the opening message; later changes are follow-ups. A title an AI
    # teammate proposed with the task starts here.
    title_calibrated: Mapped[bool] = mapped_column(
        default=False, server_default="false"
    )
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
    # Recorded when an authenticated agent opens the task's worktree. Dispatch
    # and room membership do not establish who performs the work.
    author_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # 在哪台工作电脑上做。None = 跟着所在房间的那一项选择（再往上是项目默认）；
    # 负责人改了才有自己的一份。第一次要用机器时解析一次，写进会话行，之后不变。
    compute_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 哪位 AI 队友在做：它在名册上的座位（`agent_instance_handle`）。
    # None = 所在频道的队友（频道没有自己的就是项目默认的）。
    agent_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 这条活占用的模型资源（结论 3）。NULL = 没有自己的绑定，跟项目默认走 ——
    # 见 `room_task/binding.py`，那里是唯一读这两列的地方。
    #
    # **今天只有卡片渲染读它，而且没有任何接口写它。** 写侧（一个人在卡上改绑）
    # 与执行侧（派这条活的那一刻按绑定解析模型）都要等平台有「派活」这条路径，
    # 那是 P33 的事：一条活是房间会话里的一个子 agent，今天由 agent 自己起，
    # 平台插不进去。
    #
    # 两个标量列而不是一个 JSONB：今天要存的就是这两个已知的量，JSONB 换来的只是
    # 没有 schema 校验、也写不出「这条活绑了什么」的守卫。
    #
    # 卡上**显示**哪个模型不在这里，也永远不会在这里：它从 `usage` 里这条活最后
    # 一行的 `model` 算出来，算的地方是 `presentation.card_model`。一列存「显示
    # 什么」就是第二份声明，它和真的花出去的那个迟早对不上。
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 这件事的实况文档：要做什么、做到哪、定了什么。文档不认识任务，由任务指向它。
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL", name="fk_tasks_document_id"),
        nullable=True,
    )
    # 「开始」：负责人确认讨论清楚了，芝士从这一刻起可以改动项目。三列一起写，
    # 只写一次；`started_doc_version` 是那一刻实况文档的版本，审阅时对照它。
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_doc_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 关闭时留下的一句话：做成了什么，或者为什么不做了。
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 交付标记, stamped each time a delivery merges: the latest one. An open
    # task with it set has landed earlier steps and goes on.
    accepted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the thread was collapsed. `status == closed` is the flag; this is
    # when — kept apart so "closed" needs no timestamp to be true.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The main-line message this task was made from, or whose 支线 it was made
    # in: the task shows under it. A message can have any number of tasks.
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
