"""Project models — spec §4.1, §4.4, §6.

A Project = 根话题 = one git repo. Fully independent: it owns its AI mode,
approval rules, and policies. It may link one or more Tasks to draw on a Task
Template's resource pack (and accept its conditions); an unlinked project is
fully self-governing.
"""

import enum
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Select,
    String,
    Text,
    UniqueConstraint,
    false,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


#: The database's own default for a slug: eight random hex characters.
SLUG_SQL_DEFAULT = text("substr(md5(random()::text), 1, 8)")


def random_slug() -> str:
    """Eight random lowercase letters and digits: a new project's first slug.
    Project names are mostly Chinese, and a romanised one is as likely to read
    oddly as to collide."""
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(8))


class AiMode(enum.StrEnum):
    # AI 全权负责: 开话题/干活/merge, 人可审计但不阻塞 (spec §4.4)
    autonomous = "autonomous"
    # AI 干活, 人验收 (教育红线: AI 不能验收自己做的东西)
    collaborative = "collaborative"


class Project(UuidPk, Timestamps, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200))
    # The project's name in addresses: `/projects/<slug>/tasks/318`. Unique
    # across every project; the names it had before are in `project_slugs`, so
    # a link made before a rename still finds it (`app/domain/project/address.py`).
    # A row written past the ORM (raw SQL in a script or a test) gets eight hex
    # characters from the database instead.
    slug: Mapped[str] = mapped_column(
        String(32), unique=True, default=random_slug, server_default=SLUG_SQL_DEFAULT
    )
    owner_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The team this project belongs to. Its members are the project's people and
    # its machines and quota are the project's; personal work belongs to the
    # owner's personal team. A team with projects cannot be deleted from under them.
    team_id: Mapped[int] = mapped_column(
        ForeignKey("team.id", ondelete="RESTRICT"), index=True
    )
    ai_mode: Mapped[AiMode] = mapped_column(
        Enum(AiMode, native_enum=False, length=16),
        default=AiMode.collaborative,
    )
    # The root topic of this project (its 总览/大本营). Set after creation.
    # use_alter: projects↔topics is a circular FK; add this one via ALTER.
    root_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "topics.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_projects_root_topic_id",
        ),
        nullable=True,
    )
    # The project's goals: what it is to achieve, by when, and what it leaves
    # out for now; read by every conversation's AI teammate and shown at the top
    # of 综合's overview. A document of the
    # project's own that the library does not list.
    overview_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_projects_overview_document_id",
        ),
        nullable=True,
    )
    # The 赛题 this project was created from (main's int `task` table). The 1.0
    # team-project already carries this idea as `external_task_id`; a 2.0 project
    # needs it too, or "create a project from this 赛题" produces something with
    # no way back to the 赛题 it came from. Nullable: a project made from the
    # rail belongs to no 赛题.
    external_task_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    # The agent a new topic in this project gets, and the one project-wide work
    # acts as. A project is created with it (结论 4), so NULL means only that
    # this project was made by an image that predates that — the next read seeds
    # the row through `AgentInstanceService.materialize_default`. Nullable stays
    # for exactly that window.
    # use_alter: projects↔agent_instances is a circular FK; add this one via ALTER.
    default_agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "agent_instances.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_projects_default_agent_instance_id",
        ),
        nullable=True,
    )
    # 用户自己写的一句话：这个项目打算做什么（#946 片 C，建项目时问的那一句）。
    # 这是用户的原话——平台不改写它，只把它搬进新生的房间（见 ProjectService.create）。
    intent: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Free-form policy: branch protection approvals, notify level, etc.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # The project's agent-credential generation, so that revoking advances it
    # with `UPDATE ... SET col = col + 1` — atomically, in the database. It used
    # to live in `settings`, which every settings writer replaces whole; one
    # that read before a revoke landed and flushed after it would put the old
    # generation back and every credential the revoke had just retired worked
    # again.
    agent_credential_epoch: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # Set while the owner has archived the project: it leaves every member's
    # project list, every write to it is refused (``ProjectArchivedError``), and
    # its rooms are archived with it so nothing keeps running. NULL = in use.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectMember(UuidPk, Timestamps, Base):
    """Someone in this project who is not there through its team.

    A person here is an external member: they came by an invitation they accepted,
    and they see this project and nothing else of the team. An AI teammate's seat
    is also a row here. The project's team members are never rows — they are read
    from the team, so leaving the team is leaving its projects.
    """

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_handle", name="uq_project_member"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_handle: Mapped[str] = mapped_column(String(64), index=True)


class ProjectMemberExclusion(UuidPk, Timestamps, Base):
    """「他人在小队里，但不属于这个项目」——一条记得下来的项目级事实。

    在这张表之前，「谁在这个项目里」只有两种来路写得下来：所有者
    （``projects.owner_handle``）与外部成员（``project_members`` 一行）。团队成员
    不写行，读的时候从 ``team_user_relation`` 继承——于是「在小队里」和「在这个项目
    里」成了同一句话，`退出项目` 对一个队友无从谈起，``MemberService.leave`` 只能
    409 把他推回小队（那时 ``Member.source`` 那句注释写着只有外部成员能移出）。

    「退出项目」按产品决定退的是**这个项目**：按下它就在 ``(project_id,
    user_handle)`` 上写下这一行，人还在 ``team_user_relation`` 里（那是另一个事实，
    本表一个字不动），但读「谁在这个项目里」的每一个地方都答不出他——名册、
    ``may_read_project``、``list_visible_to``。显式把他加回来（放进名册行、接受邀请）
    就是删掉这一行，所以退出不是一次性的。

    键带 ``project_id`` 而不是只有 handle：同一个人退出 A 项目，在 B 项目照常。这也
    正是它不能塞进 ``projects.settings`` 那种 JSON blob 的原因——它要能按人查。

    不出现在这里的两种人：所有者（他退不掉，``MemberService.leave`` 先拒他），以及
    没有小队、只靠外部成员行进来的人（删掉那一行就已经离开，没有别的主张要挡）。
    """

    __tablename__ = "project_member_exclusions"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "user_handle", name="uq_project_member_exclusion"
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_handle: Mapped[str] = mapped_column(String(64), index=True)


def excluded_project_ids(handle: str) -> Select[tuple[uuid.UUID]]:
    """``handle`` 已经退出的那些项目 —— 这条事实唯一的一处 SQL 写法。

    小队那条来路是**读时**继承的（``TeamUserRelation``），所以凡是从小队读出「我在
    哪些项目里」的地方，都要减掉这一句。``ProjectRepository.list_visible_to`` 就是
    拿它做的这件事；再写一遍（第二句 ``select``，或把名单查出来在 Python 里比）就是
    同一件事的第二份声明（I4a）。

    空子查询对 ``NOT IN`` 是安全的：``project_id`` 非空，名单里不会有 NULL。
    """
    return select(ProjectMemberExclusion.project_id).where(
        ProjectMemberExclusion.user_handle == handle
    )


class InvitationStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    revoked = "revoked"  # 邀请的人反悔了


class ProjectInvitation(UuidPk, Timestamps, Base):
    """一张「请你加入这个项目」的邀请，等对方回答。

    加人为什么不再是一步到位：进了项目就看得见这个项目的**全部话题**，那是别人
    的工作内容，不该由邀请方单方面决定谁能看。所以名册上多了一个中间状态——邀请
    发出去了，但人还没进来。

    答复过的邀请**留着不删**：这一行是「谁在什么时候把谁拉进来的」的唯一记录，删
    掉之后，一个突然出现在名册上的人就没有来处了。
    """

    __tablename__ = "project_invitations"
    __table_args__ = (
        # One invitation per person per project may be waiting for an answer;
        # answered ones stay as the record, as many as there were — someone can
        # be invited, leave, and be invited again.
        Index(
            "uq_project_invitation_pending",
            "project_id",
            "invitee_handle",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    invitee_handle: Mapped[str] = mapped_column(String(64), index=True)
    inviter_handle: Mapped[str] = mapped_column(String(64))
    # The inviter's own 芝士 sent it for them (`Actor.by_assistant`); the
    # invitee is told so wherever the inviter is named.
    invited_by_assistant: Mapped[bool] = mapped_column(
        default=False, server_default="false"
    )
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus, native_enum=False, length=16),
        default=InvitationStatus.pending,
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The role the invitee takes once they accept: ``member``, or ``custom``
    #: with the project's own role in ``role_id``. A waiting invitation moves
    #: with its role's holders when the role is deleted; an answered one is a
    #: record and lets go of it (``SET NULL``).
    role: Mapped[str] = mapped_column(String(16), default="member")
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "project_role_defs.id",
            ondelete="SET NULL",
            name="fk_project_invitations_role_id_project_role_defs",
        ),
        nullable=True,
        index=True,
    )


class ProjectRole(UuidPk, Timestamps, Base):
    """Someone's role in a project, when it is not the one they would hold anyway.

    Without a row a person's role follows from how they are in the project: the
    team's owner and admins are admins, everyone else is a member
    (``app.auth.project_roles``). A row says otherwise: ``admin`` for a member
    made admin, ``custom`` for one of the project's own roles, named by
    ``role_id``. A role someone holds cannot be deleted from under them
    (``RESTRICT``): they are moved to another role first. The owner is never a
    row: that is ``projects.owner_handle``.
    """

    __tablename__ = "project_roles"
    __table_args__ = (
        UniqueConstraint("project_id", "user_handle", name="uq_project_role"),
        CheckConstraint(
            "(role = 'custom') = (role_id IS NOT NULL)",
            name="ck_project_roles_custom_has_role_id",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_roles_project_id_projects",
        ),
        index=True,
    )
    user_handle: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "project_role_defs.id",
            ondelete="RESTRICT",
            name="fk_project_roles_role_id_project_role_defs",
        ),
        nullable=True,
        index=True,
    )


class ProjectRoleDef(UuidPk, Timestamps, Base):
    """A role of one project and what it may do (#4795).

    Two kinds of row, one thing — a role's definition here:

    - a built-in role (``builtin`` = ``admin`` or ``member``) whose permissions
      this project changed. No row means its defaults
      (``project_permissions.DEFAULT_GRANTS``); restoring them deletes the row.
      Its name is fixed and not stored.
    - one of the project's own roles: a ``name`` and its permissions, and a
      ``template`` mark when it was made from one (a mentor), which is how the
      platform knows it whatever it is called. A role made from a template and
      never renamed stores no name: each reader sees the template's name in
      their own language.
    """

    __tablename__ = "project_role_defs"
    __table_args__ = (
        UniqueConstraint("project_id", "builtin", name="uq_project_role_def_builtin"),
        UniqueConstraint("project_id", "name", name="uq_project_role_def_name"),
        CheckConstraint(
            "(builtin IS NOT NULL AND name IS NULL AND template IS NULL)"
            " OR (builtin IS NULL AND (name IS NOT NULL OR template IS NOT NULL))",
            name="ck_project_role_defs_builtin_or_named",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_role_defs_project_id_projects",
        ),
        index=True,
    )
    builtin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    template: Mapped[str | None] = mapped_column(String(16), nullable=True)
    permissions: Mapped[list[str]] = mapped_column(JSONB, default=list)


class ProjectTransfer(UuidPk, Timestamps, Base):
    """The owner offering the project to someone in it, until they answer.

    The project changes hands only when the recipient accepts; answered offers
    stay as the record of who handed it to whom.
    """

    __tablename__ = "project_transfers"
    __table_args__ = (
        Index(
            "uq_project_transfer_pending",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_transfers_project_id_projects",
        ),
        index=True,
    )
    from_handle: Mapped[str] = mapped_column(String(64))
    to_handle: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(
            InvitationStatus,
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_project_transfers_status",
        ),
        default=InvitationStatus.pending,
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectGitInstallation(UuidPk, Timestamps, Base):
    """One project's connected cheesex-app GitHub App installation (#192).

    A project connects to one repo at a time, and a repo to one project.
    `installation_id` is NOT unique: one org installation covers many repos,
    so several projects can share it — each token is minted for its project's
    repo alone (`GitHubAppTokens(repository_id=...)`).
    """

    __tablename__ = "project_git_installations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    installation_id: Mapped[int] = mapped_column(BigInteger)
    # "owner/repo" full name, e.g. "SageSeekerSociety/cheese". A name, so it goes
    # stale when the repository is renamed on GitHub; `repository_id` is what
    # identifies it, and `forge.follow_github_rename` refreshes this from it.
    repo: Mapped[str] = mapped_column(String(255), unique=True)
    # GitHub's numeric repository id, which a rename or transfer keeps. NULL on
    # a row bound before it was kept; the first lookup fills it in.
    repository_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # The GitHub org or user login the installation lives under.
    account: Mapped[str] = mapped_column(String(255))


class ProjectForge(UuidPk, Timestamps, Base):
    """The single authoritative repository for a project's code and proposals."""

    __tablename__ = "project_forges"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(2048))
    api_url: Mapped[str] = mapped_column(String(2048))
    repo: Mapped[str] = mapped_column(String(255))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    # Only the backend can mint credentials; the account password never leaves it.
    account_password: Mapped[str | None] = mapped_column(Text, nullable=True)


class StarterPushState(str, enum.Enum):
    pending = "pending"
    done = "done"
    failed = "failed"


class ProjectStarterPush(Base):
    """A 题目's starter code on its way into a claimed project's repository (#4767).

    The claim records which archive it took (the library row as it was that
    moment, so a later replacement reaches only later claims) and commits; the
    push itself runs in the background (`task.starter_jobs`), because unpacking
    and pushing a large archive must not hold the claim open. ``state`` is what
    the overview and the project's agents read; ``error`` is the sentence that
    says why a push could not land, as ``{key, params}``.
    """

    __tablename__ = "project_starter_pushes"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'done', 'failed')",
            name="ck_project_starter_pushes_state",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_starter_pushes_project_id_projects",
        ),
        primary_key=True,
    )
    # The archive the claim took. NULL once the publisher removed the starter
    # code (with every version of it) before the push ran.
    library_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "library_files.id",
            ondelete="SET NULL",
            name="fk_project_starter_pushes_library_file_id_library_files",
        ),
        index=True,
    )
    #: The 题目's version (`task_package_revisions.number`) at the claim.
    revision: Mapped[int] = mapped_column(Integer)
    #: The first commit's message.
    message: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default=StarterPushState.pending)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[uuid.UUID | None] = mapped_column()
    #: The starter commit the job built, recorded before it is pushed: on a later
    #: attempt, the repository's `main` holding it means the push landed.
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ForgeToken(UuidPk, Timestamps, Base):
    """Encrypted cache of the forge tokens handed to sessions."""

    __tablename__ = "forge_tokens"
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    api_url: Mapped[str] = mapped_column(String(2048))
    username: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Reads the repository and writes nothing: for a session whose work is not
    # kept. The provider expires the others; the platform revokes these
    # (`forgejo_tokens.revoke_expired_read_tokens`).
    read_only: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )


class ProjectArtifact(UuidPk, Timestamps, Base):
    """项目做出来的一样东西 —— 清单上的一行 (#1085 结论二、三)。

    **名字是身份，仓库那一项除外。** 一份报告的第 1 版和第 7 版是同一项，靠的是
    它们叫同一个名字；所以同名在这里是同一项，与资料库正相反（那边同名是两份不同
    的原件，撞了就加 `(2)`）。两边的规则相反是因为两边问的问题相反：给进来的那些
    各是一份独立的东西，做出来的这些各有一条自己的历史。

    项目那个仓库是例外，它认 `delivers_repository`：合并型的交付谁也不用起名，平台
    自己认得出是哪一项，而人随时可以把它改成想要的名字 —— 按名字找的话，改完名的
    下一次合并就会再长出一行。

    **版本不在这张表上，它是数出来的。** 一版是一次交付，所以「第 7 版」就是第 7
    张采纳了的、声明这一项的卡（`accept_artifact_version`）。存一个计数器要在每条
    合并成功的路上都记得加一、在撤回采纳的路上都记得减一，而漏掉任何一条都不会报
    错，只会让清单上的版本号和真的交出去过的东西悄悄对不上。数出来的那个数没有这
    种失效方式。

    **这张表没有路径。** 是不是产物由交付时的声明决定，不由它落在哪个目录决定
    （#1085「语义挂在声明上，不挂在目录名上」）——目录会漂，声明不会。引用型的产
    物（一次实验、一份几 GB 的数据集）本来就没有仓库路径，留一列路径出来只会让它
    们看着像缺了东西。
    """

    __tablename__ = "project_artifacts"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_project_artifact_name"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    #: 这一项就是项目的那个仓库。合并型的交付认的是这一位，不是名字 —— 名字改了
    #: 它还是同一项，而按名字找的话一次改名就会让下一次合并再长出一行。一个项目
    #: 至多一项为真（`ProjectForge` 保证一个项目只有一个仓库）。
    delivers_repository: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    #: 一句话说清这是什么东西、给谁的 —— 下一次交付靠它判断「我做的是不是它的新
    #: 一版」。写的是这样东西本身，所以它在第 1 版和第 20 版都成立；这一版做了什么
    #: 在卡的 `change_subject` 上，不在这里。
    about: Mapped[str] = mapped_column(String(80), default="", server_default="")


class RoomFileRevision(UuidPk, Timestamps, Base):
    """One saved state of a room file: the draft history a person can restore.

    Every write that goes through `room_files.save_room_file` leaves one row,
    whoever made it — a person saving in the editor, 芝士 publishing with
    `cheese show`, a restore. The bytes live content-addressed beside the room
    files (`library.revision_blob`), so two saves of the same content share one
    copy and a restore never rewrites history: it is a new row.

    This is not the artifact's version. A revision is "what the file looked
    like after this save"; a delivered version is "what a reviewer accepted",
    counted from accepted cards and kept in its own snapshot. Restoring a draft
    cannot touch the latter.
    """

    __tablename__ = "room_file_revisions"
    __table_args__ = (
        UniqueConstraint("room_id", "path", "seq", name="uq_room_file_revision_seq"),
        Index("ix_room_file_revisions_room_path", "room_id", "path"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(512))
    seq: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    author_handle: Mapped[str] = mapped_column(String(64))
    #: human | agent — who is answerable for this state of the file.
    author_kind: Mapped[str] = mapped_column(String(16))
    #: upload | ai | editor | restore | scheduled — which door the bytes came in by.
    source: Mapped[str] = mapped_column(String(16))
    #: What changed, in the author's words. 芝士 passes it with `cheese show
    #: --note`; the editor writes none.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The editor session that saved this state. A session saves more than once
    #: (each 「保存」, then on close), and its own previous save is not somebody
    #: else's change — this is how the second save tells the two apart.
    editor_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RoomFileCollab(UuidPk, Base):
    """A room file's collaborative (Yjs) state — what several people editing it
    at once are all looking at.

    A room file has no id of its own: it is ``(room_id, path)``, and its
    version is a content hash. The collaboration service names a document once
    and expects that name to keep opening the same live document, so each one
    being edited together gets this row and its own id (``id``), which is what
    the service's name carries. The row is created by opening the file in a
    live editor, not by the file existing.

    The state is only ever a step toward a version. What a reader sees — the
    preview, the history, a restore — is still the file's own revision
    (``room_file_revisions``): each store arrives here and becomes a new
    revision through ``room_files.save_room_file``. This row is the editing
    session; the revisions are the file.

    ``content`` trails the state by one store: it is the Markdown the service
    last exported, kept so a reopen hands the service the text it recognises
    instead of re-converting a document it already has (a second conversion of
    the same text is a structurally different document, and merging it in
    would double everything).
    """

    __tablename__ = "room_file_collab"
    __table_args__ = (
        UniqueConstraint("room_id", "path", name="uq_room_file_collab_room_path"),
        Index("ix_room_file_collab_room_path", "room_id", "path"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_room_file_collab_project_id_projects",
        ),
        index=True,
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "topics.id", ondelete="CASCADE", name="fk_room_file_collab_room_id_topics"
        )
    )
    path: Mapped[str] = mapped_column(String(512))
    state: Mapped[bytes] = mapped_column(LargeBinary)
    #: The Markdown the service last exported from ``state``.
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Every suggestion still waiting for a decision: ``{id, author, old, new,
    #: reason}``, as the service reported them. Rooms keep the shape documents
    #: do, so a decision here needs no second implementation.
    suggestions: Mapped[list] = mapped_column(JSONB, default=list)
    #: When this state was last stored. Assigned on every write in Python
    #: rather than left to the database: a store mutates the row in place, and
    #: a column whose name says "last edited" must not keep reading its
    #: creation time.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ProjectSlug(Base):
    """A name a project went by in addresses before its current one.

    A rename adds a row and removes none: the old name keeps leading to the
    project, and no other project can take it (a reused name would send every
    old link into someone else's project). The project itself may take one of
    its old names back, which moves it from here to `projects.slug`.
    """

    __tablename__ = "project_slugs"

    slug: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ProjectCounter(Base):
    """The last number handed out to one kind of thing in one project: tasks,
    documents and channels are each numbered from 1 within their project.

    A number is taken in the same transaction that creates the row it names, so
    a rollback leaves a gap and never a duplicate; only this one row is locked.
    """

    __tablename__ = "project_counters"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer)
