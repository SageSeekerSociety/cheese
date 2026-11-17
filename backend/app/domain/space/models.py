import uuid
from datetime import UTC, date, datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

space_seq = Sequence("space_seq")
space_categories_seq = Sequence("space_categories_seq")
space_user_rank_seq = Sequence("space_user_rank_seq")
space_admin_relation_seq = Sequence("space_admin_relation_seq")
space_domain_group_seq = Sequence("space_domain_group_seq")
space_domain_group_domain_seq = Sequence("space_domain_group_domain_seq")
space_member_seq = Sequence("space_member_seq")
space_material_seq = Sequence("space_material_seq")
space_knowledge_point_seq = Sequence("space_knowledge_point_seq")


class Space(Base):
    __tablename__ = "space"
    __table_args__ = (Index("uq_space_join_token", "join_token", unique=True),)

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
    # Who reads a 题目版 besides its owner, admins and members. NULL is a
    # members-only space, the default: its ``SpaceMember`` rows are everyone
    # else who sees it. A list of registered-email suffixes (``["ruc.edu.cn"]``)
    # is an open space: every signed-in person whose email domain is one of
    # them, or ends in ``.`` plus one, reads it and takes part without joining,
    # and its member rows are only the people outside the scope an admin named.
    # Written only by an approved ``SpaceOpenRequest`` (widening) or by the
    # owner narrowing it; the rule is ``SpaceRepository.build_reader_predicate``.
    # `none_as_null`: None writes SQL NULL, not the JSON scalar `null`, which
    # the predicate would try to read as an array.
    open_scope: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    # The space's join link (``/space-invites/<token>``), the way a member gets
    # in besides being added by an owner or admin: permanent until one of them
    # resets it, which makes the old link answer like a space that does not
    # exist. NULL until first asked for; ``SpaceService.join_link`` mints it.
    join_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Whether joining through the link waits for an owner or admin. Off by
    # default: a course's members draw on nothing, so the link alone lets a
    # student in (a team's switch defaults on for the opposite reason).
    join_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
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
    # 学情分析 (§ learning analysis): whether this board runs it. Off until a
    # manager turns it on in the settings page, and then on for every member —
    # the people already in it and the ones who join later. Who turned it on and
    # when are kept beside it, so the page can say so rather than guess. These
    # three are the whole of the switch: no per-member opt-in and no per-member
    # opt-out, which is why there is no row anywhere that says who is in.
    learning_analysis_enabled: Mapped[bool] = mapped_column(
        "learning_analysis_enabled",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    learning_analysis_enabled_by: Mapped[int | None] = mapped_column(
        "learning_analysis_enabled_by", BigInteger, nullable=True
    )
    learning_analysis_enabled_at: Mapped[datetime | None] = mapped_column(
        "learning_analysis_enabled_at", DateTime(timezone=True), nullable=True
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


class SpaceOpenRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SpaceOpenRequest(Base):
    """A space owner's request to open the space to an email-suffix scope.

    Opening a space widens who reads it from its members to everyone in the
    scope, so it waits for the platform's review, as a new space does; until a
    reviewer approves it the space keeps the mode it had. Approving copies
    ``scope`` onto ``Space.open_scope``. At most one request per space is
    pending; asking again while one waits rewrites that one, and going back to
    members-only deletes it, since nobody has reviewed it yet.
    """

    __tablename__ = "space_open_request"
    __table_args__ = (
        Index("ix_space_open_request_space_id", "space_id"),
        Index(
            "uq_space_open_request_pending",
            "space_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_space_open_request_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The suffixes the space would read to once approved.
    scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Why a reviewer sent it back; NULL otherwise.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The reviewer's handle.
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
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
    # into `app.domain.shell_catalog`, never a declaration — a 项目集 picks a 壳,
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

    两档，没有第三档：「所有成员」是**读得到这个板子的人**（``SpaceRepository.is_reader``
    判的那一批，开放空间里包括范围内的人）都能看；「仅管理员」只有所有者与管理员看得到，成员看清单时这一行
    根本不出现。档位是**读**的开关，不是写的开关 —— 两档都由管理员传。
    """

    MEMBERS = "members"
    ADMINS = "admins"


class SpaceMaterial(Base):
    """挂在题目板上的一份共用材料 (#944) —— 一个空间、一份文件、一档可见性。

    ``material`` 表自己有名字和地址、没有归属，**一份文件在不同的上下文里可以是
    不同东西**，所以归属写在另一边，这里只管「哪个板子上的哪一份」和「谁看得到」。

    **为什么不放进题目资料库：** 那是「某道题的材料」，判据是「看得见这道题的人」；
    这里是「这块板共用的资料」，判据是「这块板的成员 / 管理员」。两者的读者范围
    不同（材料可以发给没领任何题的人），放在一处会让两套判据在同一个 where 里互相
    污染。

    **为什么挂在 ``material`` 而不是 ``attachment``：** 教学配置里的
    ``material_ids``（``app.domain.task.protocol.Teaching``）references 的正是
    ``material``，题目上勾选参考资料是把这里的 ``material_id`` 直接写进去，中间
    不需要做 id 翻译。

    删除是**软删这一行**，``material`` 行与存储上的字节都留着：一块板删掉一份材料，
    不该让另一个引用它的地方变成裂图。
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
    """A user who is *in* a 题目版. In a members-only space these rows are the
    whole of who sees it besides its managers; in an open space (see
    ``Space.open_scope``) they are the people outside its scope an admin named,
    who then read it as anyone in scope does. Nobody gets a row by reading,
    claiming or publishing in an open space.

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


class SpaceJoinRequest(Base):
    """Someone asking to join a 题目版 through its link, while the link asks
    for approval.

    One kind of thing, so its own table — not the team's application table,
    whose rows belong to a team. A request is answered once: approved (the
    person becomes a member) or rejected; ``decided_by`` / ``decided_at`` say
    by whom and when. At most one request per person waits on a space at a
    time, which the partial unique index holds even when two clicks race.
    """

    __tablename__ = "space_join_request"
    __table_args__ = (
        Index(
            "uq_space_join_request_pending",
            "space_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_space_join_request_space_id", "space_id"),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_space_join_request_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id", ondelete="CASCADE", name="fk_space_join_request_space_id_space"
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
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


class SpaceKnowledgePoint(Base):
    """一条知识点，挂在一块题目板上 (§ learning analysis).

    One board writes its own list and every 题目 on the board files under it —
    a point is a property of the course, not of one exercise, so it is a row of
    the board rather than a column on each 题目. A 题目 links to these rows
    (``task_knowledge_point``), which is what makes the same point comparable
    across the whole board later.

    Deleting is **soft**: the row keeps its name and order and drops out of the
    list, because a 题目 that already files under it still has to render the
    name, and a hard delete would either break that 题目 or leave its link
    dangling. The partial unique index keeps one LIVE name per board, so the
    same point cannot be added twice and a removed one can be added back.
    """

    __tablename__ = "space_knowledge_point"
    __table_args__ = (
        # One live name per board. Partial because a soft-deleted row is the
        # record of a removal, and re-adding that same name later is a new life
        # rather than a conflict.
        Index(
            "uq_space_knowledge_point_live_name",
            "space_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 「这块板上有哪些知识点」按 space_id 找，删掉的行也要读得到（一块板列过的
        # 全部名字）。
        Index("ix_space_knowledge_point_space_id", "space_id"),
        CheckConstraint(
            "length(btrim(name)) > 0", name="ck_space_knowledge_point_name"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, space_knowledge_point_seq, primary_key=True
    )
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_knowledge_point_space_id_space",
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: 清单里的顺序，小的在前。不重排已存在的行也就不会撞号。
    display_order: Mapped[int] = mapped_column(
        "display_order", Integer, nullable=False, default=0
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


class SpaceLearningNotice(Base):
    """A member's record of confirming the 告知卡, one row per (board, person).

    A board whose 学情分析 is on shows each member a card the first time they
    enter it; the one button says 「我已阅读并知悉」, and what this row keeps is
    that they clicked it. A person who never clicked has no row, and the card
    is what they see next time — which is also what shows it to someone who was
    already in the board when a manager turned the analysis on.

    **Append-only.** ``acknowledged_at`` is written once and never updated, as
    ``app.domain.legal.models.UserConsent`` is, and kept in a table of its own
    rather than folded into that one: this is a product notice the board tells
    its own members, not a legal document the platform asks everyone to accept,
    and the two have different readers and different lifetimes.
    """

    __tablename__ = "space_learning_notice"
    __table_args__ = (
        # One row per (board, person). The unique index holds it even when two
        # clicks race, so a double-tap is a no-op and never a second record.
        Index(
            "uq_space_learning_notice_space_user",
            "space_id",
            "user_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_notice_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 点「我已阅读并知悉」的那一刻。这一列只写一次，永不更新。
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


#: 一条判断的三档（§ learning analysis, step 2）。「未涉及」不是一档：没有那一行就是它。
LEARNING_LEVELS = ("mastered", "shaky", "weak")
#: 一条提问是哪一类：问概念、贴报错、要答案、验证思路、其他。
LEARNING_QUESTION_KINDS = ("concept", "error", "answer", "verify", "other")


class SpaceLearningJudgment(Base):
    """AI 对一个成员在一块板的一个知识点上「现在怎么样」的判断（每晚分析写）。

    One row per (board, person, 知识点): the CURRENT judgment, overwritten by the
    next analysis that has something new to say about that point. History is
    not kept here — what a member sees is 「我现在在哪」, and the class view (step
    3) aggregates current rows; a trend would be a table of its own.

    A judgment always points at what it rests on: ``evidence_block_ids`` are the
    member's own messages the model cited, and the CHECK refuses an empty list,
    so 「指不出依据就不写」 holds in the schema and not only in the parser. The
    ids are not foreign keys — ``blocks`` is the hottest table in the tree and a
    key into it would lock it on every write here; a block that is gone simply
    drops out of the excerpts when the page is read.

    ``disputed_at`` is the member's 「标记不准」: the row stays (it is still the
    last thing the model said), is shown as disputed and left out of 「最该补的」
    and of any class count, and the next analysis of that point is told to
    re-check it. A fresh judgment of the point clears it.

    「未涉及」 is the absence of a row, not a fourth level.
    """

    __tablename__ = "space_learning_judgment"
    __table_args__ = (
        # One current judgment per (board, person, point). Also the index the
        # member's own page reads by (its first two columns) and the space_id
        # foreign key's index.
        Index(
            "uq_space_learning_judgment_space_user_point",
            "space_id",
            "user_id",
            "knowledge_point_id",
            unique=True,
        ),
        # Step 3 counts people per point, and removing a point cascades by it.
        Index("ix_space_learning_judgment_knowledge_point_id", "knowledge_point_id"),
        Index("ix_space_learning_judgment_misconception_id", "misconception_id"),
        CheckConstraint(
            "level IN ('mastered', 'shaky', 'weak')",
            name="ck_space_learning_judgment_level",
        ),
        CheckConstraint(
            "cardinality(evidence_block_ids) > 0",
            name="ck_space_learning_judgment_evidence",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_judgment_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    knowledge_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="CASCADE",
            name="fk_space_learning_judgment_knowledge_point_id",
        ),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 一句话归纳：为什么是这一档。给成员本人读的。
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 依据：成员自己的消息（block id），最近的在前，至多几条。
    evidence_block_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    #: 这条判断背后的那个误解（档位为不稳 / 薄弱时模型归纳，§ step 3）。老师那一侧
    #: 按它数「常见误解」的人数；掌握的那一档、或者模型说不出误解时为空。
    misconception_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_learning_misconception.id",
            ondelete="SET NULL",
            name="fk_space_learning_judgment_misconception_id",
        ),
        nullable=True,
    )
    #: 成员点了「标记不准」的时刻；下一次给出这个知识点的判断时清掉。
    disputed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 模型最近一次给出（或改写）这条判断的时刻。
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningCursor(Base):
    """How far the nightly analysis has read one member's words in one 题目.

    One row per (board, person, 题目). The position is the last message read,
    as ``(created_at, id)`` — the same order the analysis reads in, with the id
    breaking ties between messages written in the same instant — so the next
    night starts strictly after it. It moves only after the model answered and
    the answer was written; a failed call leaves it where it was, and those
    messages are read again next time.

    ``messages_read`` is how many of the member's messages this 题目 has fed in
    so far: the member's page says 「依据你在 N 道题里的 M 条发言」 from these rows.
    """

    __tablename__ = "space_learning_cursor"
    __table_args__ = (
        Index(
            "uq_space_learning_cursor_space_user_task",
            "space_id",
            "user_id",
            "task_id",
            unique=True,
        ),
        Index("ix_space_learning_cursor_task_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_cursor_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "task.id",
            ondelete="CASCADE",
            name="fk_space_learning_cursor_task_id_task",
        ),
        nullable=False,
    )
    last_block_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_block_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    messages_read: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningQuestion(Base):
    """One question a member asked, as the nightly analysis filed it.

    One row per message (``block_id``): which 题目 it was asked in, which 知识点
    it is about (NULL when the model could not place it on the list), what kind
    of question it is, and a short gist in the model's words. The member's page
    groups these into 「我常问的」; step 3 counts them per 知识点 and per 题目
    across the class (people, not messages) and groups the gists into
    recurring questions — ``cluster_id`` is that group, left NULL until step 3
    writes the clusters (and the table they point at).

    Only the member's own messages are ever filed here, and only from rooms the
    analysis reads (no private chats, no members-only channels). ``block_id``
    is not a foreign key for the reason ``SpaceLearningJudgment`` gives.
    """

    __tablename__ = "space_learning_question"
    __table_args__ = (
        Index("uq_space_learning_question_block_id", "block_id", unique=True),
        # A member's own questions in a board (「我常问的」), and the space_id
        # foreign key's index.
        Index("ix_space_learning_question_space_user", "space_id", "user_id"),
        Index("ix_space_learning_question_task_id", "task_id"),
        Index("ix_space_learning_question_knowledge_point_id", "knowledge_point_id"),
        Index("ix_space_learning_question_cluster_id", "cluster_id"),
        CheckConstraint(
            "kind IN ('concept', 'error', 'answer', 'verify', 'other')",
            name="ck_space_learning_question_kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_question_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "task.id",
            ondelete="CASCADE",
            name="fk_space_learning_question_task_id_task",
        ),
        nullable=False,
    )
    block_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    knowledge_point_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="SET NULL",
            name="fk_space_learning_question_knowledge_point_id",
        ),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 这条提问一句话的概括（模型的话，不是原文），至多几十字。
    gist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 归进的高频问题簇（``SpaceLearningCluster``）；每晚分析的聚类那一步写，还没归
    #: 进去的是 NULL。
    cluster_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_learning_cluster.id",
            ondelete="SET NULL",
            name="fk_space_learning_question_cluster_id",
        ),
        nullable=True,
    )
    #: 这条消息说出的时刻（与 block 的 created_at 相同）。
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningRun(Base):
    """One board's nightly analysis on one day: how many model calls it made.

    One row per (board, day), the day in the platform's local calendar
    (``learning_insight_analysis.LOCAL_ZONE``). ``calls`` is what the daily cap
    is held against — counted before each call is made, so a call that fails
    still counts and a gateway that keeps failing cannot spend past the cap.
    ``finished_at`` says that night's pass reached its end (every member's new
    words read, or the cap reached: ``capped``); until then the hourly tick
    comes back to it.
    """

    __tablename__ = "space_learning_run"
    __table_args__ = (
        Index("uq_space_learning_run_space_day", "space_id", "run_date", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_run_space_id_space",
        ),
        nullable=False,
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    capped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningCluster(Base):
    """一类高频问题：意思相近的几条提问归成的一簇（§ learning analysis, step 3）。

    每晚分析的聚类那一步写：把还没归类的提问（``SpaceLearningQuestion.gist``）交给
    模型，归进已有的簇或建新簇。``label`` 是这一类的代表性问题（模型的话），
    ``knowledge_point_id`` 是它多半落在的知识点（说不出就空）。老师的「高频问题」按
    问过的不同人数排 —— 那是读的时候数 ``space_learning_question`` 里归进来的行，
    不存在这里，所以这一行只在建簇时写一次。
    """

    __tablename__ = "space_learning_cluster"
    __table_args__ = (
        Index("ix_space_learning_cluster_space_id", "space_id"),
        Index("ix_space_learning_cluster_knowledge_point_id", "knowledge_point_id"),
        CheckConstraint(
            "length(btrim(label)) > 0", name="ck_space_learning_cluster_label"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_cluster_space_id_space",
        ),
        nullable=False,
    )
    knowledge_point_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="SET NULL",
            name="fk_space_learning_cluster_knowledge_point_id",
        ),
        nullable=True,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningMisconception(Base):
    """一个知识点下的一种常见误解（每晚分析随判断一起归纳）。

    模型给一条「不稳 / 薄弱」的判断时顺带说出背后的误解；它先看这个知识点已有的误
    解，能对上就用原话，对不上才说一个新的 —— 新的在这里建一行。判断行指向它
    （``SpaceLearningJudgment.misconception_id``），老师那一侧按指向它的不同成员数排
    「常见误解」。同一个知识点下同一句话只有一行。
    """

    __tablename__ = "space_learning_misconception"
    __table_args__ = (
        Index(
            "uq_space_learning_misconception_point_label",
            "knowledge_point_id",
            "label",
            unique=True,
        ),
        Index("ix_space_learning_misconception_space_id", "space_id"),
        CheckConstraint(
            "length(btrim(label)) > 0", name="ck_space_learning_misconception_label"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_misconception_space_id_space",
        ),
        nullable=False,
    )
    knowledge_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="CASCADE",
            name="fk_space_learning_misconception_knowledge_point_id",
        ),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningAdvice(Base):
    """一个知识点的讲课建议，模型写、每晚为卡住人数最多的几个知识点刷新。

    One row per 知识点: the current advice and the stuck count it was written
    for. The nightly pass rewrites it when that count or the leading
    misconceptions have moved, so reading the detail page never calls a model.
    """

    __tablename__ = "space_learning_advice"
    __table_args__ = (
        Index(
            "uq_space_learning_advice_knowledge_point_id",
            "knowledge_point_id",
            unique=True,
        ),
        Index("ix_space_learning_advice_space_id", "space_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_advice_space_id_space",
        ),
        nullable=False,
    )
    knowledge_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="CASCADE",
            name="fk_space_learning_advice_knowledge_point_id",
        ),
        nullable=False,
    )
    advice: Mapped[str] = mapped_column(Text, nullable=False)
    #: 写这条建议时卡住的人数；人数变了下一晚重写。
    stuck_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 写这条建议时排在前面的误解（id，按人数），变了也重写。
    misconception_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningWeek(Base):
    """一周里一个知识点卡住多少人：判断只存当前，趋势要靠每周记一笔（§ step 3）。

    One row per (board, 知识点, week). The nightly pass rewrites the CURRENT
    week's row after it has written the night's judgments, so by the end of a
    week the row holds that week's last count, and earlier weeks stay as they
    were. ``week_start`` is the Monday of the week in the platform's calendar
    (``learning_insight_analysis.LOCAL_ZONE``). ``involved_count`` is how many
    people the analysis had judged on the point that week — the size of the
    group, which the 「不足 3 人，不分析」 rule is held against.
    """

    __tablename__ = "space_learning_week"
    __table_args__ = (
        Index(
            "uq_space_learning_week_space_point_week",
            "space_id",
            "knowledge_point_id",
            "week_start",
            unique=True,
        ),
        Index("ix_space_learning_week_knowledge_point_id", "knowledge_point_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_week_space_id_space",
        ),
        nullable=False,
    )
    knowledge_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="CASCADE",
            name="fk_space_learning_week_knowledge_point_id",
        ),
        nullable=False,
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    stuck_count: Mapped[int] = mapped_column(Integer, nullable=False)
    involved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningExport(Base):
    """谁在什么时候导出了一份学情报告（只追加）。

    The report holds only the class as a whole, but it leaves the platform, so
    who took it, when, in which format and with which parts is kept — the
    record a teacher's export answers to. ``sections`` are the parts ticked in
    the dialog; ``knowledge_point_id`` is set when the report covered one 知识点.
    """

    __tablename__ = "space_learning_export"
    __table_args__ = (
        Index("ix_space_learning_export_space_id", "space_id"),
        Index("ix_space_learning_export_knowledge_point_id", "knowledge_point_id"),
        CheckConstraint(
            "format IN ('pdf', 'xlsx', 'docx')", name="ck_space_learning_export_format"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_export_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    sections: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    knowledge_point_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="SET NULL",
            name="fk_space_learning_export_knowledge_point_id",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
