"""SQLAlchemy rows for 本机目录授权.

Two tables, and the difference in how they are anchored is deliberate:

``local_directory_grant`` cascades from ``device``. A grant is a fact about one
machine, so a removed machine has no grants.

``local_fs_access`` deliberately does NOT. Its ``device_id`` is a bare string and
its ``grant_id`` a bare uuid, both with no foreign key, because the audit has to
outlive the things it describes: a person asking 哪台电脑、哪个目录、读写过什么 is
asking precisely at the moments when the device was removed, the grant was
revoked, or the machine never came back. An audit row that a cascade can delete
is an audit that answers the question only while the answer is uninteresting.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps
from app.domain.local_fs.paths import Platform
from app.domain.local_fs.records import Decision, GrantMode, GrantScope

__all__ = ["LocalDirectoryGrantRow", "LocalFsAccessRow"]


class LocalDirectoryGrantRow(Timestamps, Base):
    """One authorized directory on one machine.

    ``key`` is indexed on its own and is NOT the lookup: containment is decided in
    the service over normalized segments, and this index exists so that
    ``list_grants_for_device`` and the idempotency check are cheap. There is no
    query anywhere that answers which grant covers a path — that query is the
    string-prefix bug with an index in front of it.
    """

    __tablename__ = "local_directory_grant"
    __table_args__ = (
        Index("ix_local_directory_grant_device_live", "device_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Canonical text as shown to the user, and the folded key the decision uses.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=False, length=16),
        nullable=False,
    )
    mode: Mapped[GrantMode] = mapped_column(
        Enum(GrantMode, native_enum=False, length=16),
        default=GrantMode.READ,
        server_default=GrantMode.READ.value,
        nullable=False,
    )
    scope: Mapped[GrantScope] = mapped_column(
        Enum(GrantScope, native_enum=False, length=16),
        default=GrantScope.PROJECT,
        server_default=GrantScope.PROJECT.value,
        nullable=False,
    )
    # Set for a project grant and NULL for a user one. Enforced in the service
    # rather than by a CHECK, because the refusal has a message the screen shows
    # and a constraint violation has only a constraint name.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class LocalFsAccessRow(Base):
    """One line of the audit: what was asked for, and what was answered.

    Immutable — written once, never updated. There is no ``updated_at`` on
    purpose: an audit entry that could be edited is not evidence.
    """

    __tablename__ = "local_fs_access"
    __table_args__ = (
        Index("ix_local_fs_access_device_time", "device_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Bare, un-FK'd: see the module docstring.
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    grant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    path: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[GrantMode] = mapped_column(
        Enum(GrantMode, native_enum=False, length=16), nullable=False
    )
    decision: Mapped[Decision] = mapped_column(
        Enum(Decision, native_enum=False, length=16), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    actor_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
