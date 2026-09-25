import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
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


class UserSession(Base):
    """One sign-in: a refresh-token family, and one entry in the account's
    list of signed-in devices.

    Only a digest of the refresh token is stored. Each refresh replaces
    ``current_hash`` and keeps the digest it replaced in ``previous_hash``, so
    a token presented again after it was rotated away is recognised: within
    the grace window as a concurrent refresh, after it as a copy in someone
    else's hands.
    """

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    previous_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # The credential that completed the sign-in: ``password``, ``passkey``,
    # ``totp``, ``backup_code``, ``email_code``, ``signup`` or
    # ``oauth:<provider>``.
    login_method: Mapped[str] = mapped_column(String(64), nullable=False)
    # Two-step verification was due and a trusted device stood in for it, so
    # ``login_method`` is the first step alone.
    two_factor_skipped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    user_agent: Mapped[str] = mapped_column(String(1024), nullable=False)
    ip: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Until when this sign-in may get a sudo ticket without proving a
    # credential again: set by a sudo verification, and by a sign-in whose
    # credential sudo itself would have accepted.
    sudo_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserTrustedDevice(Base):
    """A browser the account owner chose not to be asked 2FA on again.

    The browser holds a random token in a cookie; only its SHA-256 digest is
    stored, as for refresh tokens. A trust lasts a fixed 30 days from when it
    was granted, however often it is used.
    """

    __tablename__ = "user_trusted_devices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The sign-in this browser made most recently with the trust: what the
    # device list marks as trusted, and what signing that device out ends.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("user_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_agent: Mapped[str] = mapped_column(String(1024), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
