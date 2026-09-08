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
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
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
    # fusion P4: the 知是 Team this project is the AI workspace for (nullable — a
    # personal project has none). Lets a team page open its Project natively.
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("team.id", ondelete="SET NULL"), nullable=True, index=True
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
    # acts as. NULL = the implicit 芝士 (handle `cheese`, no type) — which is
    # what every project had before agents were pickable, so nothing has to be
    # backfilled for a project to resolve.
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
    # 一页纸总结 (spec §7.3/F2): AI-maintained one-pager, 老师 30 秒读懂。
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Free-form policy: branch protection approvals, notify level, etc.
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # When the 本体 last ran a heartbeat — used to schedule ≤1 patrol/day/project.
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectRole(enum.StrEnum):
    lead = "lead"  # 组长
    member = "member"
    mentor = "mentor"  # 导师


class ProjectMember(UuidPk, Timestamps, Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_handle", name="uq_project_member"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_handle: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[ProjectRole] = mapped_column(
        Enum(ProjectRole, native_enum=False, length=16),
        default=ProjectRole.member,
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
        # 同一个人在同一个项目里只能有一张**待答复**的邀请。约束落在
        # (project, invitee, status) 上而不是 (project, invitee)：拒绝过之后必须
        # 还能再邀一次，而部分索引在 SQLite 上不通用，所以用这个三元组——
        # 一张 pending 加任意多张已答复的，正好是要允许的形状。
        UniqueConstraint(
            "project_id", "invitee_handle", "status", name="uq_project_invitation"
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    invitee_handle: Mapped[str] = mapped_column(String(64), index=True)
    inviter_handle: Mapped[str] = mapped_column(String(64))
    role: Mapped[ProjectRole] = mapped_column(
        Enum(ProjectRole, native_enum=False, length=16),
        default=ProjectRole.member,
    )
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
