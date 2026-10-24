"""A task owns one branch, worktree and PR inside a collaboration room."""

import enum
import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class TaskStatus(enum.StrEnum):
    """Whether this piece of work is still going.

    Two values, because two is what is actually observable: work is either being
    done or it is not. Where the work stands while it is open is its teammate's
    own report (working, waiting, blocked), `TaskProgress`, kept beside this and
    never folded into it; while a card waits on review the card says it (等审).

    A task may deliver in several steps: accepting the step its card calls the
    last closes the task, accepting an earlier one leaves it open
    (`review/task_landing.py`). A task may also close without delivery.
    `accepted_at` records the latest approval and `delivered_head` the latest
    merged revision, kept even if that approval is later revoked.
    """

    open = "open"
    closed = "closed"


class TaskProgress(enum.StrEnum):
    """Where an open task's work stands between the facts the platform can
    check: its AI teammate says it (`cheese_task(action=state)`), and two of the
    platform's own actions set it (a card returned or voided puts the task back
    to ``working``; a card filed clears it).

    A refinement of an open task, never a state of its own beside `TaskStatus`:
    a task is open or closed, and while a card waits on its review the card says
    where it stands (等审), whatever was reported before. So the teammate never
    says 「待审阅」 or 「关闭」 — those are facts, not reports — and while a card
    is open it cannot report at all (`room_task.progress`). The board reads this
    where the task's own teammate's session does not speak
    (`presentation.task_presentation`): a teammate waiting on a build is still
    on it after its session stops, and while its session runs, a report stands
    only in the run it was made in (`Task.progress_run_*`).
    """

    #: Doing the work.
    working = "working"
    #: Waiting on something it started — a build, CI, a long command.
    waiting = "waiting"
    #: Cannot go on without a person.
    blocked = "blocked"


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


def people_of(
    owner_handle: str | None, contributor_handles: Iterable[str] | None
) -> tuple[str, ...]:
    """This task's people — the owner first, then its contributors.

    A question asked inside a task and landing on no one person waits on them:
    whose work this is, and who answers for it. Both places that record it read
    this one list (`agent/announce.py`'s ``notify_question`` writes the
    notification, `api/routes/awaiting.py` the 待办). A channel or 支线 has no
    such list, so a question there still lands on nobody.

    The owner leads because he is 唯一的主 (``owner_handle``); a contributor who
    is also the owner, or listed twice, is one person here.

    A function rather than a property alone because rows read as plain columns
    (`room_task/repositories.py`'s `ListedTask`) are not `Task` instances, and
    「等谁」 must be the same list for both: two implementations would be two
    answers to one question.
    """
    people: list[str] = []
    for handle in (owner_handle, *(contributor_handles or ())):
        name = (handle or "").strip()
        if name and name not in people:
            people.append(name)
    return tuple(people)


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
        Index("ix_tasks_source_block_id", "source_block_id"),
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
    # ``created_by``'s own 芝士 opened it for them (`Actor.by_assistant`): the
    # task is theirs, and its page says their 芝士 made it.
    created_by_assistant: Mapped[bool] = mapped_column(
        default=False, server_default="false"
    )
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
    # The last check state the PR poll saw on this task's PR: the head and
    # ``running`` / ``green`` / ``red``. Kept in the row so a restart still
    # knows the previous tick and the running → green step is not lost.
    pr_checks_head: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pr_checks_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_check_detail: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 新进来的 AI 队友在这个任务里用的执行环境（`agent.environment`）。None = 跟
    # 所在频道的默认（再往上是项目默认）。改它不搬任何人：每位队友自己的那一份在
    # `agent_environments`。
    default_environment: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
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
    # 关闭时留下的一句话：做成了什么，或者为什么不做了。
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What the AI teammate last said about where its work stands
    # (`TaskProgress`), with its note, when and by whom. Only the teammate
    # writes these (`TaskService.set_progress`); nothing the platform observes
    # about its sessions does. NULL until it first says.
    progress_state: Mapped[TaskProgress | None] = mapped_column(
        Enum(
            TaskProgress,
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_tasks_progress_state",
        ),
        nullable=True,
    )
    progress_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    progress_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    progress_set_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Which run of ``progress_set_by``'s session the report was made in, as
    # its runner names it (`X-Cheese-Run`: harness, epoch and seq of the
    # ``running`` event that began the run): while that seat's session runs,
    # the report stands only in that run. NULL for the platform's own writes
    # and for a call that named no run (a harness whose runner names none).
    progress_run_harness: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress_run_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress_run_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # When the teammate's session ended after its last report: the board says
    # 「已离开」 beside what it said, and never rewrites it. Cleared when the
    # teammate reports again.
    agent_left_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 交付标记, stamped each time a delivery merges: the latest one. An open
    # task with it set has landed earlier steps and goes on.
    accepted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Its last delivery landed and its AI teammate is writing it up: the task
    # closes when the teammate closes it, or at the deadline
    # (`room_task.closing`). Shown as accepted meanwhile; still open, so the
    # teammate can write the task document.
    closing_since: Mapped[datetime | None] = mapped_column(
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
    # The message whose words the task was made from: the one turned into it,
    # or the person's message the turn its AI teammate created it in was
    # answering. Unlike `upgraded_from_block_id` it can be a reply in a 支线 or
    # a message inside another task, so it says where the task came from, not
    # where it shows. Empty for a task someone created on its own.
    source_block_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "blocks.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_tasks_source_block_id_blocks",
        ),
        nullable=True,
    )

    @property
    def people(self) -> tuple[str, ...]:
        """This task's people — see :func:`people_of`, which is the same list."""
        return people_of(self.owner_handle, self.contributor_handles)
