"""Feedback — 用户与 agent 对平台本身说的话.

`docs/topics/反馈功能后端设计-方案稿.md` is the reasoning behind these tables,
and `frontend/src/views/feedback/*` reads them over the routes in
`app/api/routes/feedback*.py` — screen and schema are now the same feature, not a
prototype of one.

Two decisions in here are worth reading before touching the table:

**The primary key is a uuid, the human number is a separate column.** `FB-1042`
is `display_no`, handed out by a PG sequence so two concurrent submits cannot
collide — an application-level `max()+1` can. A uuid needs no coordination at
all, so it stays the key. Two jobs, two columns.

**`session_id` / `environment` / `logs` / `what_happened` / `repro` / `evidence`
are snapshots, not foreign keys.** For `environment`/`logs` there is no table to
point at (the environment is a JSON column on `topics`, the log intake has no
`Base` at all). For `session_id` there IS a table and the snapshot is still the
right answer: a harness's own session id lives on `agent_sessions.resume_token`,
whose own comment says it is an opaque string not to be parsed, and sessions get
archived and machines get torn down — a foreign key into that would make a
feedback row unreadable once the session it is *evidence about* is gone, which is
exactly the row you most want to still be able to read.

The same reasoning is already written twice in this repo: `TopicProgress`'s
docstring ("a turn that dies mid-flight must not take the progress with it") and
the `consumed_turn` note on `Block`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

# The human-facing number behind `FB-1042`. A PG sequence rather than
# `max(display_no)+1`, same shape as `task_seq` / `notification_seq`.
feedback_seq = Sequence("feedback_seq")


class FeedbackKind(enum.StrEnum):
    bug = "bug"
    suggestion = "suggestion"
    other = "other"


class FeedbackStatus(enum.StrEnum):
    """`received → in_progress → resolved → deployed`.

    Four rungs, one per thing that actually changes for the person who filed it:
    收录（有人看到了）、处理（有人在动）、修复（改完了）、上线（能用了 —— 服务端
    界面上的词是 `已修复` / `已上线`）。
    `triaging` / `planned` were the fifth and sixth rungs for a while and were
    dropped — they described the team's internal queue, and to a reader they read
    as three shades of 「还没好」.

    `deployed` is not a synonym for `resolved`, and the two are deliberately on
    the ladder together rather than folded into one 「办完了」: resolved is the
    commit, deployed is the release, and the gap between them is exactly what the
    person who filed it comes back to ask about. Folding them would tell a
    reporter that 「已解决」 means the fix is in their hands, which it is not.

    「处理中」 is one rung and stays one rung: how many internal steps there are
    and who is on which of them is the team's business, not the reporter's. Admin
    granularity goes in `assignee_handle` and `priority`, not into the status.

    `received` rather than `new`: the frontend carries a colour table
    (`STATUS_META`) keyed on these strings, and `new` would collide with nothing
    but would have to be renamed in two frontend tables for nothing.

    **Retiring a value is not free.** The column is a plain VARCHAR
    (`_enum` below), so dropping `triaging` / `planned` needs no migration — but
    any row still holding one cannot be read back, and the load fails rather than
    returning something odd. Nothing shipped with those values (this feature was
    merged with the four), so this is a dev-database concern; a live one would
    want a data migration ahead of the code.
    """

    received = "received"
    in_progress = "in_progress"
    resolved = "resolved"
    deployed = "deployed"


class FeedbackPriority(enum.StrEnum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class FeedbackVisibility(enum.StrEnum):
    """Where the item is readable.

    `private` is a superset of "only the admins": the submitter sees their own
    private items too. The server decides that with a WHERE clause, not a second
    flag — see `services.visible_to`.
    """

    public = "public"
    private = "private"


def _enum(enum_cls: type[enum.Enum]) -> Enum:
    """A column type that lands as a plain VARCHAR(16).

    `native_enum=False` plus no `create_constraint`, which is what
    `alerts.level` / `alerts.kind` already do in
    `alembic/versions/cf93e4735e4a_full_schema.py`. The alternative (a PG enum
    type created by its own migration) would make adding a sixth status a
    migration, and the status set is the part of this feature most likely to
    move. The other lesson is `TopicStatus`'s: a value that enters a PG enum can
    never be deleted, and 「an enum that cannot read its own history turns a
    restore into a crash」.
    """
    return Enum(enum_cls, native_enum=False, length=16)


class Feedback(UuidPk, Timestamps, Base):
    __tablename__ = "feedback"
    #: `display_no` is generated by the database (a `nextval` the INSERT renders),
    #: so a plain flush leaves the attribute unloaded and the first read of
    #: `display_id()` tries to lazy-load it — which, past the await, is an error
    #: rather than a slow query. `eager_defaults` asks for it in the INSERT's
    #: RETURNING clause: same round trip, value present.
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        # Named here rather than via `unique=True`: the migration has to write a
        # name regardless, and an unnamed constraint in the model is how the two
        # files start describing different things.
        UniqueConstraint("display_no", name="uq_feedback_display_no"),
        # The public list, in every sort it is asked for. `(visibility, status,
        # created_at)` serves the status-filtered tabs; `(visibility, created_at)`
        # is the unfiltered one, which `resolved`-沉底 still ranges on.
        Index(
            "ix_feedback_visibility_status_created",
            "visibility",
            "status",
            "created_at",
        ),
        Index("ix_feedback_visibility_created", "visibility", "created_at"),
        # The admin's four columns: 公开 / 私密非安全 / agent 来源 / 安全.
        Index(
            "ix_feedback_visibility_security_created",
            "visibility",
            "security",
            "created_at",
        ),
        # Agent-originated feedback is the minority of rows and gets its own
        # column in the admin UI, so the partial index stays small.
        Index(
            "ix_feedback_agent_created",
            "created_at",
            postgresql_where=text("author_is_agent"),
        ),
        # 「我的反馈」— two readers, one index each: what I wrote, what I filed.
        Index("ix_feedback_author_created", "author_handle", "created_at"),
        Index("ix_feedback_submitted_by_created", "submitted_by_handle", "created_at"),
        # 「指派给我的」 is one of the admin's most-used entries, and most rows
        # have no assignee at all.
        Index(
            "ix_feedback_assignee_created",
            "assignee_handle",
            "created_at",
            postgresql_where=text("assignee_handle IS NOT NULL"),
        ),
    )

    display_no: Mapped[int] = mapped_column(
        Integer, feedback_seq, server_default=feedback_seq.next_value(), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300))
    # The one line the list shows. The prototype takes it from the body's first
    # 60 characters; storing it means the list query never reads `problem`.
    summary: Mapped[str] = mapped_column(String(300), default="")

    kind: Mapped[FeedbackKind] = mapped_column(_enum(FeedbackKind))
    status: Mapped[FeedbackStatus] = mapped_column(
        _enum(FeedbackStatus), default=FeedbackStatus.received
    )
    visibility: Mapped[FeedbackVisibility] = mapped_column(_enum(FeedbackVisibility))
    priority: Mapped[FeedbackPriority] = mapped_column(
        _enum(FeedbackPriority), default=FeedbackPriority.normal
    )
    # Security-relevant: triaged by a person, never by the model. Read as a
    # refinement of `private` (see `services.visible_to`), not as a second switch.
    security: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # What the reporter (or the agent, on their behalf) wrote.
    problem: Mapped[str] = mapped_column(Text, default="")
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    expectation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The three sections only an agent can fill honestly: what it saw, how to
    # reproduce it, what it has as proof.
    what_happened: Mapped[str | None] = mapped_column(Text, nullable=True)
    repro: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The "attach current session logs and environment" checkbox's snapshot.
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)

    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # No `index=True`: `ix_feedback_author_created` starts with this column, and
    # a bare single-column index beside it is a second index for the same
    # reader — two things that can disagree.
    author_handle: Mapped[str] = mapped_column(String(64))
    # Kept alongside the handle for joins and counts. NULL whenever the credential
    # was in the handle-only family (a cheesex session token has no int id).
    author_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    author_is_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    # Who pressed submit, when that is not who found it — an agent proposal a
    # person approved. NULL on a direct human submission.
    submitted_by_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignee_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Pointers back to where this happened. `SET NULL`, not `CASCADE`: the
    # feedback is about the platform and outlives the room it was filed from.
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    # MVP stores tags as a JSON list rather than a `feedback_tags` table
    # (方案稿 §8.8): the prototype has a list of strings and no filtering yet.
    # Same shape as `Alert.payload` / `TopicProgress.items` / `Block.meta`.
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FeedbackSupport(UuidPk, Base):
    """One person, one vote. The shape is `BlockReaction`'s, not the table.

    `UniqueConstraint(feedback_id, author_handle)` is what makes a repeat
    support a no-op instead of a second row — the same job
    `uq_block_reaction(block_id, emoji, author)` does one table over.
    """

    __tablename__ = "feedback_supports"
    __table_args__ = (
        UniqueConstraint("feedback_id", "author_handle", name="uq_feedback_support"),
    )

    # Covered by `uq_feedback_support(feedback_id, author_handle)`, which leads
    # with this column: every query that reads it also constrains it.
    feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE")
    )
    # Same reason, second column of the same constraint.
    author_handle: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class FeedbackComment(UuidPk, Base):
    """A reply on a feedback item. Two levels, enforced in the service.

    `parent_id` points at a TOP-LEVEL comment, never at a reply — the service
    re-parents a reply-to-a-reply onto its grandparent, exactly as the
    prototype's `stores/feedback.ts::addComment` does (`parent?.parentId ??
    parent?.id`). That keeps reads non-recursive and keeps the three comment
    layouts (flat / threaded / 楼中楼) agreeing about the same tree.
    """

    __tablename__ = "feedback_comments"
    __table_args__ = (
        Index("ix_feedback_comments_feedback_created", "feedback_id", "created_at"),
        Index("ix_feedback_comments_parent_id", "parent_id"),
    )

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE")
    )
    # Self-referential and CASCADE: deleting a top-level comment takes its
    # replies with it, because a reply with no parent has nowhere to render.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("feedback_comments.id", ondelete="CASCADE"), nullable=True
    )
    author_handle: Mapped[str] = mapped_column(String(64))
    author_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    author_is_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FeedbackTimeline(UuidPk, Base):
    """Append-only status events — 「什么时候到过这里」, not 「现在在哪」.

    `Feedback.status` and this table are two copies of one fact, and they must be
    written together. The prototype already learned this the hard way
    (`stores/feedback.ts::setStatus`: 「只改状态不写时间线，右侧那根 Timeline
    就会和卡片上的状态词对不上」). The server's answer is to take the decision
    away from the route layer: status changes happen inside one service method
    that appends the event in the same transaction, and no route exposes a
    direct write to `status`.

    The same status CAN appear twice (a revert, then forward again). The
    prototype's timeline reads the EARLIEST occurrence — it records when the
    item first arrived somewhere. Keeping both rows answers 「到过这里几次」,
    which is the question the table can answer and the prototype's can't.
    """

    __tablename__ = "feedback_timeline"
    __table_args__ = (Index("ix_feedback_timeline_feedback_at", "feedback_id", "at"),)

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE")
    )
    status: Mapped[FeedbackStatus] = mapped_column(_enum(FeedbackStatus))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    by_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FeedbackNote(UuidPk, Base):
    """Internal admin notes — append-only, never shown to the reporter.

    Deliberately not a `internal_note` string column on `feedback`: one string
    between two admins overwrites each other and keeps neither an author nor a
    timestamp. The UI still renders one note (the latest); that is a presentation
    choice, not a reason to lose the others.
    """

    __tablename__ = "feedback_notes"
    __table_args__ = (
        Index("ix_feedback_notes_feedback_created", "feedback_id", "created_at"),
    )

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE")
    )
    author_handle: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class FeedbackReadState(UuidPk, Timestamps, Base):
    """Per-user read cursor over feedback activity (方案稿 §6.2(b)).

    Feedback deliberately does NOT go into `alerts`: `Alert.project_id` is NOT
    NULL and feedback is about the platform, not about a project — so the
    nullability would have to be relaxed on a table that is the root of the
    whole inbox semantics. A cursor is the cheaper half of that trade, and it
    has a precedent one domain over (`TopicReadState`: 「cheap to bump, cheap to
    count against」).

    One global row per person, not one per item: the bell asks 「我的反馈有没有
    新动静」, which is answerable by counting events newer than this cursor on the
    items I filed or was assigned.
    """

    __tablename__ = "feedback_read_states"
    __table_args__ = (
        UniqueConstraint("user_handle", name="uq_feedback_read_state_user"),
    )

    # The unique constraint already builds this index.
    user_handle: Mapped[str] = mapped_column(String(64))
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeedbackProposalDismissal(UuidPk, Base):
    """「这个不用」，记下来 —— 每条提案只该问一次。

    原型里的「不用」只把组件状态置成 `dismissed`，刷新就回来。服务端必须落一行，
    否则**同一个问题每轮都会再问一遍**，而这是刷屏最主要的来源：它会让人对整张卡
    产生免疫，然后是整个反馈入口。

    指纹而不是 block id：同一个问题换一种说法提上来，人不想再看第二遍。指纹算
    「发生了什么 + 怎么复现」归一化之后的哈希（`services.proposal_fingerprint`），
    只在新提案进来时查一次。

    `topic_id` 带 CASCADE：这是话题里的一个判断，话题没了它就没有意义。范围是话题
    而不是全平台 —— 同一个问题在另一个话题里遇到，那里的人还没被问过。
    """

    __tablename__ = "feedback_proposal_dismissals"
    __table_args__ = (
        UniqueConstraint(
            "topic_id", "fingerprint", name="uq_feedback_proposal_dismissal"
        ),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    dismissed_by_handle: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
