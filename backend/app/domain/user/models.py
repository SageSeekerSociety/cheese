from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class User(Base):
    """Minimal mapping for the core user table used for auth.

    NOTE: This intentionally only maps fields needed for password-based
    authentication and profile lookup. Additional columns can be added as
    needed during later migration steps.
    """

    __tablename__ = "user"
    # Case-insensitive, and only among live accounts: a deleted account's name
    # and email are free to be taken again.
    __table_args__ = (
        Index(
            "uq_user_username_lower",
            func.lower(text("username")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_user_email_lower",
            func.lower(text("email")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    email_domain: Mapped[str | None] = mapped_column(
        "email_domain", String, nullable=True
    )
    hashed_password: Mapped[str | None] = mapped_column(
        "hashed_password", String, nullable=True
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


class UserProfile(Base):
    __tablename__ = "user_profile"
    __table_args__ = (
        Index(
            "uq_user_profile_user_id",
            "user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column("user_id", Integer, nullable=False)
    nickname: Mapped[str] = mapped_column(String, nullable=False)
    intro: Mapped[str] = mapped_column(String, nullable=False)
    avatar_id: Mapped[int] = mapped_column("avatar_id", Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserRealNameIdentity(Base):
    """Minimal mapping for user_real_name_identities to support Task.requireRealName checks."""  # noqa: E501

    __tablename__ = "user_real_name_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)

    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    real_name: Mapped[str] = mapped_column("real_name", String, nullable=False)
    student_id: Mapped[str] = mapped_column("student_id", String, nullable=False)
    grade: Mapped[str] = mapped_column("grade", String, nullable=False)
    major: Mapped[str] = mapped_column("major", String, nullable=False)
    class_name: Mapped[str] = mapped_column("class_name", String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserTwoFactor(Base):
    """A user's TOTP second factor, and the setup waiting to be confirmed.

    Both secrets are sealed with ``app.core.crypto`` under their own purposes,
    bound to the user. The factor is enabled exactly when ``secret`` is set.
    """

    __tablename__ = "user_two_factor"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    always_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    pending_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserBackupCode(Base):
    """One unused 2FA backup code, as a keyed digest; using it deletes the row."""

    __tablename__ = "user_backup_code"
    __table_args__ = (
        UniqueConstraint("user_id", "key_id", "digest", name="uq_user_backup_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)
    digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
