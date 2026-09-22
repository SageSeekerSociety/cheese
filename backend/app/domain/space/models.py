from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Sequence,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

space_seq = Sequence("space_seq")
space_categories_seq = Sequence("space_categories_seq")
space_user_rank_seq = Sequence("space_user_rank_seq")
space_admin_relation_seq = Sequence("space_admin_relation_seq")
space_domain_group_seq = Sequence("space_domain_group_seq")
space_domain_group_domain_seq = Sequence("space_domain_group_domain_seq")
space_member_seq = Sequence("space_member_seq")
space_invite_code_seq = Sequence("space_invite_code_seq")


class Space(Base):
    __tablename__ = "space"

    id: Mapped[int] = mapped_column(BigInteger, space_seq, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    intro: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Existing spaces stay approved; the public creation route assigns PENDING.
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="APPROVED", server_default="APPROVED"
    )
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enable_rank: Mapped[bool] = mapped_column(
        "enable_rank", Boolean, nullable=False, default=False
    )
    # There is no visibility tier. Who can see a 题目版 exists is answered by
    # membership and nothing else — see ``SpaceMember``.
    visible_task_limit: Mapped[int | None] = mapped_column(
        "visible_task_limit", Integer, nullable=True
    )
    # Kotlin Space.defaultCategory -> default_category_id column
    default_category_id: Mapped[int | None] = mapped_column(
        "default_category_id", Integer, nullable=True
    )
    announcements: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    task_templates: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
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


class SpaceCategory(Base):
    """A 题目分组 *inside* one 题目版 — not a course.

    A 题目版 (`Space`) is a course / an activity; this row is one bucket of
    that course's problems — 作业, 实验, 小测 — so one course holds several of
    them and every 题目 (`Task`) hangs off exactly one. The auto-created
    "General" row a new board receives (`SpaceService.create_space`) is the
    course's first bucket, not a course in its own right.

    This is the current product definition. `docs/spec.md` still carries the
    older reading (Space = 机构, SpaceCategory = 一门课), which the
    course-template redefinition retired: the two levels each moved up one, and
    code and comments are being brought to the new one. Where the protocol and
    壳 modules below still say 项目集, they mean *this* row.

    **The 机构协议 fields live here (#370).** `resource_pack` / `conditions` /
    `default_role` and the 壳 `shell` are declared once on the 分组 and inherited
    by every 题目 underneath it, with a per-题目 whole-key override
    (`Task.protocol_override`). That is why they sit at this level rather than on
    the 题目版: a course states its terms once for 作业 as a whole, not once per
    exercise. Read them only through `app.domain.task.protocol.resolve` (and
    `app.domain.shell.service` for the 壳) — never off the column directly.
    """

    __tablename__ = "space_categories"

    id: Mapped[int] = mapped_column(BigInteger, space_categories_seq, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(
        "display_order", Integer, nullable=False, default=0
    )
    # Archived flag (SpaceCategory.isArchived in Kotlin)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 机构协议 (spec §4.2, #370): a 项目集 is where an institution states what it
    # provides and what it asks in return — 创研课 2026 秋 has twenty 赛题 and one
    # set of terms, so this is configured once here rather than twenty times
    # below. A single 赛题 may override it (`task.protocol_override`).
    # Resolved by app.domain.task.protocol.resolve; never read directly.
    resource_pack: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    conditions: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    default_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 壳: which of the platform's 壳 the projects under this 项目集 run. A NAME
    # into `app.domain.shell.catalog`, never a declaration — a 项目集 picks a 壳,
    # it does not ship one. Same placement and same whole-key override as the
    # three protocol fields above (a 创研课 has twenty 赛题 and one 壳). NULL =
    # nobody said, so `default` is in force.
    shell: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 课程级教学配置 (#8d772257): 本周范围、课程级 system prompt 模板、课件与知识
    # 材料的引用。A 创研课 teaches, and what it is teaching this week is a
    # property of the 项目集 — twenty 赛题 under one 教学安排. Rides the same
    # override chain as the three above, via the same resolve(); empty for every
    # 项目集 that is not a course.
    teaching: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
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


class SpaceUserRank(Base):
    __tablename__ = "space_user_rank"

    id: Mapped[int] = mapped_column(BigInteger, space_user_rank_seq, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceAdminRole(int, Enum):
    OWNER = 0
    ADMIN = 1


class SpaceAdminRelation(Base):
    __tablename__ = "space_admin_relation"

    id: Mapped[int] = mapped_column(
        BigInteger, space_admin_relation_seq, primary_key=True
    )
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceDomainGroup(Base):
    __tablename__ = "space_domain_group"

    id: Mapped[int] = mapped_column(
        BigInteger, space_domain_group_seq, primary_key=True
    )
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceDomainGroupDomain(Base):
    __tablename__ = "space_domain_group_domain"

    id: Mapped[int] = mapped_column(
        BigInteger, space_domain_group_domain_seq, primary_key=True
    )
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
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


class SpaceClassificationTagRelation(Base):
    """Mirrors NT's `space_classification_topics_relation`. Many-to-many between
    Space and Topic, used by the frontend's `Space.classificationTopics` field.
    """

    __tablename__ = "space_classification_tag_relation"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
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


class SpaceMember(Base):
    """A user who is *in* a 题目版 — the whole of who can see it.

    Membership and the admin relation are two different questions: this row
    says "this 题目版 is mine to see and I am counted among its members",
    ``SpaceAdminRelation`` says "I may manage it". The creator and any admins
    therefore keep seeing it without a row here, and removing a member here
    takes nothing else away — not their tasks, not their submissions, not
    their projects.
    """

    __tablename__ = "space_member"
    __table_args__ = (
        # At most one LIVE row per (space, person). Without this, two requests
        # landing together both read "not a member" and both write — and then
        # every later `get_member` raises `MultipleResultsFound` instead of
        # answering, which turns the documented double-tap no-op into a 500.
        # Partial because a soft-deleted row is the record of a removal and a
        # re-join revives it rather than adding a second: those rows are
        # outside the index, so a removal does not block the join that follows.
        Index(
            "uq_space_member_active",
            "space_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The pair lookup itself, deleted rows included: `_get_any_member`
        # reads the pair without the `deleted_at` filter (that is the point of
        # it), so the partial index above cannot serve it, and `list_members`
        # reads a whole space by the leading column.
        Index("ix_space_member_space_user", "space_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, space_member_seq, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceInviteCode(Base):
    """A code that, when redeemed, makes the redeemer a member of a 题目版.

    Every 题目版 is created holding one, because a 题目版 nobody else can
    reach is not much use: the creator hands the code out and the people who
    take it are the ones who can see the board at all.

    Deliberately NOT the platform's ``invite_code`` table (that one admits a
    person to the platform at registration and has nothing to do with any
    space); the two share a shape and nothing else, so they share no table.
    """

    __tablename__ = "space_invite_code"

    id: Mapped[int] = mapped_column(BigInteger, space_invite_code_seq, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
