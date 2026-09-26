"""Project models — spec §4.1, §4.4, §6.

A Project = 根话题 = one git repo. Fully independent: it owns its AI mode,
approval rules, and policies. It may link one or more Tasks to draw on a Task
Template's resource pack (and accept its conditions); an unlinked project is
fully self-governing.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AiMode(enum.StrEnum):
    # AI 全权负责: 开话题/干活/merge, 人可审计但不阻塞 (spec §4.4)
    autonomous = "autonomous"
    # AI 干活, 人验收 (教育红线: AI 不能验收自己做的东西)
    collaborative = "collaborative"


class Project(UuidPk, Timestamps, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200))
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
    # 一页纸总结 (spec §7.3/F2): AI-maintained one-pager, 管理员 30 秒读懂。
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 用户自己写的一句话：这个项目打算做什么（#946 片 C，建项目时问的那一句）。
    # 与 summary 的分工是「谁说的」：summary 是 AI 维护的一页纸，这一条是用户的
    # 原话——平台不改写它，只把它搬进新生的房间（见 ProjectService.create）。
    intent: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Free-form policy: branch protection approvals, notify level, etc.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # When the 本体 last ran a heartbeat — used to schedule ≤1 patrol/day/project.
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
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
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus, native_enum=False, length=16),
        default=InvitationStatus.pending,
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectGitInstallation(UuidPk, Timestamps, Base):
    """One project's connected cheesex-app GitHub App installation (#192).

    Both `project_id` and `installation_id` are unique: a project connects to
    one repo at a time, and one installation (= one GitHub-side "connect this
    App to this repo" grant) is never shared between two platform projects —
    otherwise a token minted for it would be ambiguous about which project's
    git operations it belongs to.
    """

    __tablename__ = "project_git_installations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    installation_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    # "owner/repo" full name, e.g. "SageSeekerSociety/cheese".
    repo: Mapped[str] = mapped_column(String(255))
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


class ForgeToken(UuidPk, Timestamps, Base):
    """Encrypted cache of access tokens with provider-enforced expiration."""

    __tablename__ = "forge_tokens"
    project_id: Mapped[uuid.UUID] = mapped_column(index=True)
    api_url: Mapped[str] = mapped_column(String(2048))
    username: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


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
