import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
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
task_attachment_seq = Sequence("task_attachment_seq")


class Task(Base):
    __tablename__ = "task"

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
        # (`app.auth.domains.task`)，可见性判据也为每道候选题和每个读者打同样的
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
    """Minimal mapping for task_submission_schema table (ElementCollection in Kotlin)."""  # noqa: E501

    __tablename__ = "task_submission_schema"

    task_id: Mapped[int] = mapped_column(
        "task_id", BigInteger, ForeignKey("task.id"), primary_key=True
    )
    index: Mapped[int] = mapped_column("index", Integer, primary_key=True)
    description: Mapped[str] = mapped_column("description", String, nullable=False)
    type: Mapped[int] = mapped_column("type", SmallInteger, nullable=False)


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
    submitter_id: Mapped[int] = mapped_column(
        "submitter_id", Integer, nullable=False
    )  # references user.id

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
    )

    id: Mapped[int] = mapped_column(
        BigInteger, task_submission_review_seq, primary_key=True
    )
    submission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_submission.id"), nullable=False
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


class TaskAttachment(Base):
    """一个文件挂在一道题上 —— 出题时带上的材料，领取者与审核者可以下载。

    **不是** ``attachment`` 表本身，也不是它的替代：那一行文件仍然在
    ``attachment`` 里（type/url/meta，meta 里带着 filename / contentType /
    storageKey / size / uploaderId），由 ``POST /attachments`` 上传、由
    ``app.core.storage`` 落到存储上。这张表只回答「它属于哪道题」以及题目这一侧
    的事情（下载次数、什么时候挂上、什么时候被拿下来）。两件事分开，是因为同一个
    文件在不同的上下文里可以是不同东西：交作业时附上的那份属于某个 ``submission``，
    出题人放在题目上的那份属于某道 ``task``，共用一行文件记录不等于共用一个语义。

    删除是**软删这一行**，存储上的对象留着 —— 见 ``TaskAttachmentService.remove``
    的说明。
    """

    __tablename__ = "task_attachment"

    id: Mapped[int] = mapped_column(BigInteger, task_attachment_seq, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id"), nullable=False
    )
    # ``attachment.id`` 是 Integer（autoincrement），外键类型必须一致。
    attachment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("attachment.id"), nullable=False
    )
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
