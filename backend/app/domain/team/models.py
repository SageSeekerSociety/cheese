from datetime import datetime
from enum import Enum

from sqlalchemy import (
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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

team_seq = Sequence("team_seq")
team_user_relation_seq = Sequence("team_user_relation_seq")
team_membership_application_seq = Sequence("team_membership_application_seq")

# Which rows hold a user's one personal team. ``uq_team_personal_owner`` is unique
# over these, and the insert in ``TeamRepository.create_personal_team`` names the
# same predicate so Postgres can match its ON CONFLICT to that index.
PERSONAL_TEAM_ROW = text("personal_owner_user_id IS NOT NULL AND deleted_at IS NULL")

# A membership that was not deleted. Every reader of this table filters it, and
# it is what keeps `ix_team_user_relation_team_user` from holding people who
# left. The migration building that index spells the same text out again.
LIVE_TEAM_MEMBER_ROWS = text("deleted_at IS NULL")


class TeamVisibility(str, Enum):
    """Who can find a team without being in it.

    PUBLIC teams show up in search and open by id; STEALTH teams do neither and
    are reached only through the team's join link. How joining then works is
    the team's ``join_approval`` — the same switch a project has.
    """

    PUBLIC = "public"
    STEALTH = "stealth"


#: The plan every new team starts on.
DEFAULT_PLAN_KEY = "free"


class Team(Base):
    __tablename__ = "team"
    __table_args__ = (
        Index("ix_team_name", "name"),
        Index("uq_team_handle_lower", func.lower(text("handle")), unique=True),
        Index(
            "uq_team_personal_owner",
            "personal_owner_user_id",
            unique=True,
            postgresql_where=PERSONAL_TEAM_ROW,
        ),
        # A shared team is named by its own handle; a personal team is named by
        # its owner's username (see ``team_handle``), so it stores none.
        CheckConstraint(
            "(personal_owner_user_id IS NULL) = (handle IS NOT NULL)",
            name="ck_team_handle_iff_shared",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, team_seq, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # The team's name in URLs and mentions: the same alphabet as a username and
    # the same namespace, so one handle names one user or one team, never both.
    handle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intro: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    avatar_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # A user's personal single-member team (v4: 个人 = 单人真团队).
    # NULL = a normal shared team; set = the personal team of that user (one per user,
    # auto-provisioned). Personal projects belong to it, and it can own compute like
    # any team — so 为自己注册设备 is just 注册给个人团队.
    personal_owner_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TeamVisibility.PUBLIC.value,
        server_default=TeamVisibility.PUBLIC.value,
    )
    # The team's join link (``/team-invites/<token>``): permanent until an owner
    # or admin resets it. NULL until first asked for.
    join_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    # Whether joining — by link or from the profile — waits for an owner or
    # admin. Off, the person is in the moment they confirm.
    join_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # The credit plan the team is on (``usage.models.Plan``). Every team, a
    # personal one too, starts on Free.
    plan_key: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plans.key"),
        nullable=False,
        default=DEFAULT_PLAN_KEY,
        server_default=DEFAULT_PLAN_KEY,
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

    @property
    def open_to_all(self) -> bool:
        """Whether someone outside this team may see it: a public, shared team.

        One predicate behind ``TeamService.visible_team``, the team search, and
        every payload that names a team to a stranger. A personal team is one
        person's; a stealth team is reached through its join link.
        """
        return (
            self.personal_owner_user_id is None
            and self.visibility == TeamVisibility.PUBLIC.value
        )


class TeamMemberRole:
    OWNER = 0
    ADMIN = 1
    MEMBER = 2


class TeamUserRelation(Base):
    __tablename__ = "team_user_relation"
    __table_args__ = (
        # Team access asks "is this person in this team" on every request
        # (`app.auth.domains.team`, `app.auth.domains.knowledge`), task
        # visibility asks it inside an EXISTS for every candidate task, and the
        # team roster reads it the same way. That is why the index starts at
        # `team_id` — it also pays off `team_user_relation.team_id`'s
        # foreign-key-without-index debt. Readers that filter by `user_id`
        # alone (`UserStatisticsRepository.count_teams`,
        # `TeamRepository.list_teams_of_user`) are not served by it; the
        # migration says why they are left alone.
        Index(
            "ix_team_user_relation_team_user",
            "team_id",
            "user_id",
            postgresql_where=LIVE_TEAM_MEMBER_ROWS,
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, team_user_relation_seq, primary_key=True
    )
    team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("team.id"), nullable=False
    )
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


class ApplicationType(str, Enum):
    REQUEST = "REQUEST"
    INVITATION = "INVITATION"


class ApplicationStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    CANCELED = "CANCELED"


class TeamMembershipApplication(Base):
    __tablename__ = "team_membership_application"

    id: Mapped[int] = mapped_column(
        BigInteger, team_membership_application_seq, primary_key=True
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("team.id"), nullable=False
    )
    initiator_id: Mapped[int] = mapped_column(Integer, nullable=False)

    type: Mapped[str] = mapped_column(String(length=255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(length=255), nullable=False, default="PENDING"
    )
    role: Mapped[str] = mapped_column(
        String(length=255), nullable=False, default="MEMBER"
    )

    message: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class RecruitmentStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class TeamRecruitmentPost(Base):
    __tablename__ = "team_recruitment_post"
    __table_args__ = (
        Index("ix_recruitment_team_id", "team_id"),
        Index("ix_recruitment_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("team.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_members: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="OPEN")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
