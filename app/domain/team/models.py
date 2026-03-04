from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    DateTime,
    Sequence,
    BigInteger,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarative for team domain."""


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

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class TeamMemberRole:
    OWNER = 0
    ADMIN = 1
    MEMBER = 2


class TeamUserRelation(Base):
    __tablename__ = "team_user_relation"

    id: Mapped[int] = mapped_column(BigInteger, team_user_relation_seq, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("team.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


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

    id: Mapped[int] = mapped_column(BigInteger, team_membership_application_seq, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("team.id"), nullable=False)
    initiator_id: Mapped[int] = mapped_column(Integer, nullable=False)

    type: Mapped[str] = mapped_column(String(length=255), nullable=False)
    status: Mapped[str] = mapped_column(String(length=255), nullable=False, default="PENDING")
    role: Mapped[str] = mapped_column(String(length=255), nullable=False, default="MEMBER")

    message: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
