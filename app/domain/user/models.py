from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Sequence, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class User(Base):
    """Minimal mapping for the core user table used for auth.

    NOTE: This intentionally only maps fields needed for password-based
    authentication and profile lookup. Additional columns can be added as
    needed during later migration steps.
    """

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column("hashed_password", String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column("user_id", Integer, nullable=False)
    nickname: Mapped[str] = mapped_column(String, nullable=False)
    intro: Mapped[str] = mapped_column(String, nullable=False)
    avatar_id: Mapped[int] = mapped_column("avatar_id", Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserFollowingRelationship(Base):
    """Mapping for user_following_relationship table."""

    __tablename__ = "user_following_relationship"

    id: Mapped[int] = mapped_column(
        Integer,
        Sequence("user_following_relationship_id_seq"),
        primary_key=True,
    )
    followee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    follower_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRealNameIdentity(Base):
    """Minimal mapping for user_real_name_identities to support Task.requireRealName checks."""

    __tablename__ = "user_real_name_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)

    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    real_name: Mapped[str] = mapped_column("real_name", String, nullable=False)
    student_id: Mapped[str] = mapped_column("student_id", String, nullable=False)
    grade: Mapped[str] = mapped_column("grade", String, nullable=False)
    major: Mapped[str] = mapped_column("major", String, nullable=False)
    class_name: Mapped[str] = mapped_column("class_name", String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRealNameAccessLog(Base):
    __tablename__ = "user_real_name_access_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accessor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    module_type: Mapped[str | None] = mapped_column(String, nullable=True)
    module_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    access_reason: Mapped[str] = mapped_column(String, nullable=False)
    ip_address: Mapped[str] = mapped_column(String, nullable=False)
    access_type: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
