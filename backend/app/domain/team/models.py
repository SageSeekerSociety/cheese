from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

team_seq = Sequence("team_seq")
team_user_relation_seq = Sequence("team_user_relation_seq")
team_membership_application_seq = Sequence("team_membership_application_seq")


class Team(Base):
    __tablename__ = "team"
    __table_args__ = (Index("ix_team_name", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, team_seq, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    intro: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    avatar_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TeamMemberRole:
    OWNER = 0
    ADMIN = 1
    MEMBER = 2


class TeamUserRelation(Base):
    __tablename__ = "team_user_relation"

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
