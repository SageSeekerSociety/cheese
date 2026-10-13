from datetime import UTC, datetime
from enum import Enum

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
space_material_seq = Sequence("space_material_seq")


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
    task_templates: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    # 空间级教学配置 (#944): the same `teaching` key the 项目集 carries
    # (`SpaceCategory.teaching`), one level further out. It is the DEFAULT for
    # every 题目 on the board — a teacher writes "本周范围 / system prompt /
    # 课件" once for the whole 空间, and any 项目集 or 题目 may replace it
    # whole. Same shape, same reader: `app.domain.task.protocol.resolve` reads
    # this level first and lets the ones below override it. Empty for every board
    # that predates the key.
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


class SpaceCategory(Base):
    """A 题目分组 *inside* one 题目版 — not a course.

    A 题目版 (`Space`) is a course / an activity; this row is one bucket of
    that course's problems — 作业, 实验, 小测 — so one course holds several of
    them and every 题目 (`Task`) hangs off exactly one. The auto-created
    first category a new board receives (`SpaceService.create_space`) is the
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


class SpaceMaterialVisibility(str, Enum):
    """谁能看到这一份空间资料。

    两档，没有第三档：「所有成员」是**这个板子里的人**（``SpaceRepository.is_member``
    判的那一批）都能看；「仅管理员」只有所有者与管理员看得到，成员看清单时这一行
    根本不出现。档位是**读**的开关，不是写的开关 —— 两档都由管理员传。
    """

    MEMBERS = "members"
    ADMINS = "admins"


class SpaceMaterial(Base):
    """挂在题目板上的一份共用材料 (#944) —— 一个空间、一份文件、一档可见性。

    和 ``task_attachment`` 同构，理由也一样：``material`` 表自己有名字和地址、
    没有归属，**一份文件在不同的上下文里可以是不同东西**，所以归属写在另一边，
    这里只管「哪个板子上的哪一份」和「谁看得到」。

    **为什么不复用 ``task_attachment``：** 那是「某道题的附件」，判据是「看得见这道
    题的人」；这里是「这块板共用的资料」，判据是「这块板的成员 / 管理员」。两者
    的读者范围不同（材料可以发给没领任何题的人），写在一张表上会让两套判据在
    同一个 where 里互相污染。

    **为什么挂在 ``material`` 而不是 ``attachment``：** 教学配置里的
    ``material_ids``（``app.domain.task.protocol.Teaching``）references 的正是
    ``material``，题目上勾选参考资料是把这里的 ``material_id`` 直接写进去，中间
    不需要做 id 翻译。

    删除是**软删这一行**，``material`` 行与存储上的字节都留着 —— 同
    ``TaskAttachmentService.remove`` 的理由：一道题删掉附件不该让另一个引用它的
    地方变成裂图。
    """

    __tablename__ = "space_material"
    # 两个索引与迁移 ``c1f7a09b34d2`` 一一对齐。挪进模型是因为这里是
    # ``Base.metadata`` 的来源：少写一个，下一次 ``alembic revision --autogenerate``
    # 就会生一条「删掉这个索引」的假迁移。同 ``SpaceMember`` / ``SpaceAnnouncement``。
    __table_args__ = (
        # 同一块板上同一份文件只挂一次。部分索引（``deleted_at IS NULL``）：撤下来
        # 之后再传回去是允许的，那时它是一条新的生命。
        Index(
            "uq_space_material_live",
            "space_id",
            "material_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 「这份文件在不在某块板的仅管理员档里」—— 通用读路由 ``GET /materials/{id}``
        # 每次都要问一句，按 ``material_id`` 找。
        Index("ix_space_material_material_id", "material_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, space_material_seq, primary_key=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("space.id"), nullable=False
    )
    # ``material.id`` 是 Integer（autoincrement），外键类型必须一致。
    # ``ondelete="CASCADE"``：素材行被 ``DELETE /materials/{id}`` 删掉时，这条关联
    # 跟着走 —— 关联指的是那份文件，文件没了它就无从谈起。没有它这条外键会把一条
    # 既有接口打坏（那张表是整张 schema 里唯一指向 ``material.id`` 的外键，删除
    # 会撞 500）。
    material_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("material.id", ondelete="CASCADE"), nullable=False
    )
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SpaceMaterialVisibility.MEMBERS.value
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
    # 这个人是怎么进来的: the ``space_invite_code`` row this membership came from,
    # or NULL for "no code is on record". Two different situations share that
    # NULL and this column cannot tell them apart, so nothing downstream should
    # try to:
    #
    # * rows written before this column existed — nobody recorded which code
    #   was taken, and the migration deliberately backfills nothing (naming a
    #   code for an old member would be inventing history);
    # * someone the owner put in directly (``SpaceService.add_member``), where
    #   the truth is "no code was involved".
    #
    # Both read as 未知, which is not the same claim as "no code", and is the
    # only honest thing to say for the first group.
    #
    # No FK, like every other id in this module; and the code it names stays
    # readable after a revoke, because revoking is a soft delete.
    invite_code_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

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
    # Who minted it. Nullable because a row written before the field was read
    # back — or by a caller that had no actor — may hold nobody; the roster
    # renders that as 未知 rather than as an empty name.
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 这张码给谁 / 干什么用, in the maker's own words ("十月这批同学"). Optional:
    # a code that says nothing about itself is a normal code, and every code
    # minted before this column is exactly that.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceAnnouncement(Base):
    """One 公告 on a 题目版, a row of its own.

    One row each, so two managers editing two announcements at once touch two
    rows, and publishing one can notify the space about exactly that one
    (``announcement_service``). Deleting is a real delete: an announcement that
    was taken down is not kept anywhere a reader could come across it.

    ``author_id`` is who published it. Nullable only because the announcements
    written before this table named their publisher by nickname, and the one
    that matched none of that space's managers kept no author.
    """

    __tablename__ = "space_announcement"
    __table_args__ = (Index("ix_space_announcement_space", "space_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Rich text, the editor's HTML.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: From this moment on it is 已到期: it leaves the task list and folds away
    #: on the announcements page. NULL = it does not expire.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Moves when what it says changes (title, body, expiry) and not when it is
    #: pinned: 已编辑 is a claim about the text.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
