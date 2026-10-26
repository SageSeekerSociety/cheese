import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.domain.task.indexed_rows import LIVE_ROWS, SWEEPABLE_ROWS

task_seq = Sequence("task_seq")
task_membership_seq = Sequence("task_membership_seq")
task_tags_relation_seq = Sequence("task_tags_relation_seq")
task_submission_seq = Sequence("task_submission_seq")
task_submission_entry_seq = Sequence("task_submission_entry_seq")
task_submission_review_seq = Sequence("task_submission_review_seq")
task_submission_schema_seq = Sequence("task_submission_schema_seq")
task_access_domain_seq = Sequence("task_access_domain_seq")


class Task(Base):
    __tablename__ = "task"
    __table_args__ = (
        CheckConstraint("start_mode IN ('DIRECT', 'APPLY')", name="ck_task_start_mode"),
    )

    id: Mapped[int] = mapped_column(BigInteger, task_seq, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    intro: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # 机构协议 per-赛题 override (#370 option (c)). NULL / absent keys inherit the
    # 项目集's terms; a present key REPLACES it wholesale (a half-merged resource
    # pack is harder to reason about than either source). Accepted keys:
    # resource_pack / conditions / default_role / shell — see
    # app.domain.task.protocol.
    protocol_override: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Foreign keys as simple ids for now; detailed relationships can be added later.
    creator_id: Mapped[int] = mapped_column("creator_id", Integer, nullable=False)
    space_id: Mapped[int] = mapped_column("space_id", BigInteger, nullable=False)
    category_id: Mapped[int] = mapped_column("category_id", BigInteger, nullable=False)

    # Business fields relevant for listing/filtering/sorting.
    submitter_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    approved: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    participant_limit: Mapped[int | None] = mapped_column(
        "participant_limit", Integer, nullable=True
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registration_start_at: Mapped[datetime | None] = mapped_column(
        "registration_start_at", DateTime(timezone=True), nullable=True
    )
    default_deadline: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # How a claim of this task starts (``inputs.START_DIRECT`` / ``START_APPLY``):
    # straight away with the task's own deadline, or on the publisher's approval
    # with ``default_deadline`` days from then.
    start_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="APPLY", server_default="APPLY"
    )
    resubmittable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rank: Mapped[int | None] = mapped_column("rank", Integer, nullable=True)
    require_real_name: Mapped[bool] = mapped_column(
        "require_real_name", Boolean, nullable=False, default=False
    )
    min_team_size: Mapped[int | None] = mapped_column(
        "min_team_size", Integer, nullable=True
    )
    max_team_size: Mapped[int | None] = mapped_column(
        "max_team_size", Integer, nullable=True
    )
    reject_reason: Mapped[str] = mapped_column(
        "reject_reason", String, nullable=False, default=""
    )
    team_locking_policy: Mapped[str] = mapped_column(
        "team_locking_policy", String(50), nullable=False, default="NO_LOCK"
    )
    team_id: Mapped[int | None] = mapped_column("team_id", BigInteger, nullable=True)
    access_control_enabled: Mapped[bool] = mapped_column(
        "access_control_enabled", Boolean, nullable=False, default=False
    )
    # 评分要点（``TaskRubricItem``）给不给领题的人看。批改的人（出题人、空间管理员）
    # 总能看到；关掉它只是不对学生显示。
    rubric_public: Mapped[bool] = mapped_column(
        "rubric_public", Boolean, nullable=False, default=True, server_default="true"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        "published_at", DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        "ended_at", DateTime(timezone=True), nullable=True
    )
    # 谁在什么时候审的这道题（审核走 PATCH /tasks/{id}，approve 与 reject 都写这里）。
    # 不能拿 updated_at 顶：改标题、改截止也刷它。可空 —— 这两列是后加的，已经审过
    # 的老题没有，那时 reviewed_at 是 NULL，不代表没审过（审没审过看 approved）。
    reviewed_by: Mapped[int | None] = mapped_column(
        "reviewed_by", Integer, nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        "reviewed_at", DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskAccessDomain(Base):
    __tablename__ = "task_access_domain"

    id: Mapped[int] = mapped_column(
        BigInteger, task_access_domain_seq, primary_key=True
    )
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskMembership(Base):
    __tablename__ = "task_membership"
    __table_args__ = (
        # 鉴权每次请求都问一次「这道题 × 这个人领没领」
        # (`app.auth.task_roles`)，可见性判据也为每道候选题和每个读者打同样的
        # 相关 EXISTS (`visibility_service`)：两者都按 task_id + member_id 取，
        # 所以这一列 `task_id` 也还上了 `task_membership.task_id` 的外键无索引债。
        Index(
            "ix_task_membership_task_member",
            "task_id",
            "member_id",
            postgresql_where=LIVE_ROWS,
        ),
        # 一个人名下的全部领取（个人主页计数、队伍变更前的锁检查）按 member_id 取。
        Index("ix_task_membership_member", "member_id", postgresql_where=LIVE_ROWS),
        # 截止清扫按 deadline 找「还没交、已过期」的领取，谓词与它同一份
        # (`indexed_rows.SWEEPABLE_ROWS`)。
        Index(
            "ix_task_membership_deadline",
            "deadline",
            postgresql_where=SWEEPABLE_ROWS,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, task_membership_seq, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id"), nullable=False
    )
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    participant_uuid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )

    approved: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_team: Mapped[bool] = mapped_column(
        "is_team", Boolean, nullable=False, default=False
    )

    email: Mapped[str] = mapped_column(String, nullable=False, default="")
    phone: Mapped[str] = mapped_column(String, nullable=False, default="")

    # 应征说明 (#370): why this team fits the 赛题. The one field the cheesex
    # market had that 知是's 领取 did not — everything else about claiming
    # (approval, quota, real-name, team locking) already lived here.
    pitch: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    # The task's mentors (handles) the claimant saw and agreed to when they
    # applied or started (#3830): the ones seated in the project the claim
    # opens. Someone added to the task afterwards is not, without new consent.
    # NULL on claims made before it was recorded, which seat nobody.
    agreed_mentors: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    completion_status: Mapped[str] = mapped_column(
        "completion_status", String(50), nullable=False, default="NOT_SUBMITTED"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskSubmissionSchemaEntry(Base):
    """Minimal mapping for task_submission_schema table (ElementCollection in Kotlin)."""

    __tablename__ = "task_submission_schema"

    task_id: Mapped[int] = mapped_column(
        "task_id", BigInteger, ForeignKey("task.id"), primary_key=True
    )
    index: Mapped[int] = mapped_column("index", Integer, primary_key=True)
    description: Mapped[str] = mapped_column("description", String, nullable=False)
    type: Mapped[int] = mapped_column("type", SmallInteger, nullable=False)
    # Which of the task's stages (``TaskStage.index``) this item is handed in
    # under. 0 on a task without stages: it is one implicit stage.
    stage: Mapped[int] = mapped_column(
        "stage", Integer, nullable=False, default=0, server_default="0"
    )


def new_stage_key() -> str:
    """A stage's id: stable for the stage's life, unique within its task."""
    return secrets.token_hex(6)


class TaskStage(Base):
    """One stage of a task, as its publisher set it: a name, what it asks for,
    and when it is due. A task's submission items and rubric lines each belong
    to a stage, and a claim hands work in stage by stage; a stage is done when
    one of its submissions is accepted (``submission_state``). A task without
    rows here is one implicit stage, due by the claim's own deadline. Replaced
    as a whole, like the submission form (``app.domain.task.stages``)."""

    __tablename__ = "task_stages"
    __table_args__ = (
        UniqueConstraint("task_id", "key", name="uq_task_stages_task_id_key"),
    )

    task_id: Mapped[int] = mapped_column(
        "task_id",
        BigInteger,
        ForeignKey("task.id", name="fk_task_stages_task_id_task"),
        primary_key=True,
    )
    index: Mapped[int] = mapped_column("index", Integer, primary_key=True)
    # The stage's id, stable across reorders: what a writer sends back so the
    # submissions under a stage follow it (``stages.replace_stages``).
    key: Mapped[str] = mapped_column(
        "key", String(16), nullable=False, default=lambda: new_stage_key()
    )
    name: Mapped[str] = mapped_column("name", String(64), nullable=False)
    description: Mapped[str] = mapped_column(
        "description", Text, nullable=False, default=""
    )
    due_at: Mapped[datetime | None] = mapped_column(
        "due_at", DateTime(timezone=True), nullable=True
    )


class TaskRubricItem(Base):
    """One line of a task's 评分要点: what is scored, how many points, and what
    earns them. Read by whoever grades the submissions, and by the students when
    the task's ``rubric_public`` says so. Replaced as a whole, like the
    submission form above."""

    __tablename__ = "task_rubric_item"

    task_id: Mapped[int] = mapped_column(
        "task_id",
        BigInteger,
        ForeignKey("task.id", name="fk_task_rubric_item_task_id_task"),
        primary_key=True,
    )
    index: Mapped[int] = mapped_column("index", Integer, primary_key=True)
    item: Mapped[str] = mapped_column("item", String(64), nullable=False)
    points: Mapped[int] = mapped_column("points", Integer, nullable=False)
    criteria: Mapped[str] = mapped_column("criteria", Text, nullable=False, default="")
    # The stage (``TaskStage.index``) whose submissions this line scores.
    stage: Mapped[int] = mapped_column(
        "stage", Integer, nullable=False, default=0, server_default="0"
    )


class TaskMentor(Base):
    """Someone the task's publisher chose to join every claimer's project as a
    mentor (#3830). The claimer agrees to it before claiming; the project made
    for the claim seats them as mentors."""

    __tablename__ = "task_mentor"

    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("task.id", ondelete="CASCADE", name="fk_task_mentor_task_id_task"),
        primary_key=True,
    )
    user_handle: Mapped[str] = mapped_column(String(64), primary_key=True)


class TaskTagRelation(Base):
    """Minimal mapping for task_tag_relation — which 标签 a 赛题 is filed under."""

    __tablename__ = "task_tag_relation"

    id: Mapped[int] = mapped_column(
        BigInteger, task_tags_relation_seq, primary_key=True
    )
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id"), nullable=False
    )
    tag_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskSubmission(Base):
    """Minimal mapping for task_submission table."""

    __tablename__ = "task_submission"
    __table_args__ = (
        # 一条领取的历次提交：完成状态那条轴每问一次「手上有活吗」就打一组
        # EXISTS，题目板、分析视图和提交仓库都按 membership_id 取（`repositories`）。
        # 这一列也是 `task_submission.membership_id` 的外键索引。
        Index("ix_task_submission_membership_id", "membership_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, task_submission_seq, primary_key=True)
    membership_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_membership.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # The stage (``TaskStage.index``) this version hands in. Versions count
    # across stages, so a membership's version still names one submission.
    stage: Mapped[int] = mapped_column(
        "stage", Integer, nullable=False, default=0, server_default="0"
    )
    submitter_id: Mapped[int] = mapped_column(
        "submitter_id", Integer, nullable=False
    )  # references user.id
    #: Which version of the 题目 this was handed in under
    #: (``TaskPackageRevision.number``); None for the ones from before versions.
    package_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskSubmissionEntry(Base):
    """Minimal mapping for task_submission_entry table."""

    __tablename__ = "task_submission_entry"
    __table_args__ = (
        # 一版提交的逐项作答，读提交时按 task_submission_id 取
        # (`repositories.TaskSubmissionEntryRepository.list_by_submission_id`)。
        # 也是这一列的外键索引。
        Index("ix_task_submission_entry_submission_id", "task_submission_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, task_submission_entry_seq, primary_key=True
    )
    task_submission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_submission.id"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_text: Mapped[str | None] = mapped_column(
        "content_text", Text, nullable=True
    )
    content_attachment_id: Mapped[int | None] = mapped_column(
        "content_attachment_id",
        Integer,
        nullable=True,
        comment="References attachment.id; kept nullable for pure-text entries.",
    )
    # The name of the form item this entry answered, as the form read when the
    # entry was written. The form can be reordered, renamed or cut down later;
    # the entry keeps the name it was answered under. NULL when that item had
    # no name, or the form had no item at this position.
    prompt: Mapped[str | None] = mapped_column("prompt", Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskSubmissionReview(Base):
    """Minimal mapping for task_submission_review table."""

    __tablename__ = "task_submission_review"
    __table_args__ = (
        # 一版提交的评审：完成状态那条轴的相关 EXISTS、提交列表和回填都按
        # submission_id 取。也是这一列的外键索引。
        Index("ix_task_submission_review_submission_id", "submission_id"),
        # 一版提交可以有多条评审 —— 每位老师各判自己那一份。唯一性是
        # `(submission_id, reviewer_id)`（同一人在同一版上只有一条），**只看活行**：
        # 软删一条再重判是允许的，所以约束是带 `deleted_at IS NULL` 的部分唯一索引。
        Index(
            "uq_task_submission_review_submission_reviewer",
            "submission_id",
            "reviewer_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, task_submission_review_seq, primary_key=True
    )
    submission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_submission.id"), nullable=False
    )
    # 谁判的（`user.id`）。历史行在迁移里回填 `-id`（当年一版只有一条评审，
    # 判者不明，负数保证每行唯一、也不会撞到真实用户 id）。
    reviewer_id: Mapped[int] = mapped_column(
        "reviewer_id", Integer, nullable=False, default=0
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskPackageRevision(Base):
    """One version of what a 题目 hands its takers (#4767 §一): its description,
    its 阶段, what to hand in, its 评分要点 and its files. The first is written when the
    题目 is published; every change to any of those writes the next, saying in
    ``changes`` what changed, so a project taken from it can say 「题目已更新到第
    N 版」 and what that version changed. A submission records the version it
    was made under (``TaskSubmission.package_revision``).

    ``changes`` names the parts: ``published`` on the first; ``description``,
    ``stages``, ``submission``, ``rubric`` when those changed; ``files`` with the names
    ``added``, ``replaced`` and ``removed``; ``starter`` with ``added``,
    ``replaced`` or ``removed``.
    """

    __tablename__ = "task_package_revisions"
    __table_args__ = (
        Index(
            "uq_task_package_revisions_task_id_number",
            "task_id",
            "number",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "task.id",
            ondelete="CASCADE",
            name="fk_task_package_revisions_task_id_task",
        ),
        nullable=False,
    )
    #: 第几版，从 1 数。
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Who made the change (``user.id``).
    author_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: What the 题目's library held at this version: each name mapped to the
    #: ``library_files.id`` it was then, so the history page can hand out the
    #: files of any version. Null on versions recorded before it was kept.
    files: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class TaskDraft(Base):
    """A 题目 written but not yet published, or the changes to a published one
    not yet saved (``task_id``). It holds the publish form as the person left
    it, so the form survives a reload, and it is what their 芝士 writes into:
    an answer goes on after its page is closed, so what it fills has to be
    somewhere other than that page (``app.domain.task.drafts``).

    ``fields`` is the form, in the shape ``POST /tasks`` takes. ``version``
    counts the writes; ``marks`` holds, per field, the version of the person's
    last write to it, so a write by 芝士 made from an older reading leaves a
    field the person has since changed as they left it.
    """

    __tablename__ = "task_draft"
    __table_args__ = (
        # A person's drafts in a space, latest first: the draft list.
        Index("ix_task_draft_user_space", "user_id", "space_id", "updated_at"),
        Index("ix_task_draft_space_id", "space_id"),
        Index("ix_task_draft_task_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE", name="fk_task_draft_user_id_user"),
        nullable=False,
    )
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("space.id", ondelete="CASCADE", name="fk_task_draft_space_id_space"),
        nullable=False,
    )
    # The published 题目 these are changes to; NULL for a new one.
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("task.id", ondelete="CASCADE", name="fk_task_draft_task_id_task"),
        nullable=True,
    )
    # The 芝士 conversation working on it, when there is one. Not a foreign key:
    # the conversation is the assistant's, and a draft outlives a deleted one.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Where it came from, as the list shows it: a file's name, or empty.
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    marks: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
