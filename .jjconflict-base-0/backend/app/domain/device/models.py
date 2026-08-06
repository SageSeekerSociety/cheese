"""SQLAlchemy models backing the device flow (SQL repository).

Tables mirror the storage-agnostic dataclasses in ``repository.py``:
``device`` (enrolled compute machines + durable token), ``device_auth_code``
(short-lived device-flow codes), ``device_project`` (device↔project assignments),
``device_team`` (device↔team bindings — compute belongs to the team, v4) and
``device_topic`` (a topic's pinned device — affinity, v4). ``SqlDeviceRepository``
converts between these rows and the dataclasses; the service never sees them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps


class DeviceRow(Base):
    __tablename__ = "device"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The durable device token. TODO(security): store a hash and deliver the raw
    # token once via poll, so a DB read alone cannot impersonate a device.
    token: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NOTE: a device is PURE COMPUTE (execution-architecture v3: AIPool ⊥ ComputePool;
    # a self-hosted device is a ComputePool node). It carries NO agent identity — the
    # agent a screen acts as is resolved per project/topic (fusion-design §5: agent =
    # screen), independent of which machine hosts it.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DeviceAuthCodeRow(Base):
    __tablename__ = "device_auth_code"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # pending | approved
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DeviceProjectRow(Timestamps, Base):
    """A device↔project assignment: which projects a device may run agents in.
    The device's owner manages these (many-to-many)."""

    __tablename__ = "device_project"
    __table_args__ = (
        UniqueConstraint("device_id", "project_id", name="uq_device_project"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)


class DeviceTeamRow(Timestamps, Base):
    """A device↔team binding (execution-architecture v4: compute belongs to the team).
    A device bound to a team is usable by every project of that team — 为团队注册设备.
    The device's owner manages these (many-to-many). ``team_id`` is a bare BigInteger
    (no cross-table FK, matching ``device_project.project_id``)."""

    __tablename__ = "device_team"
    __table_args__ = (UniqueConstraint("device_id", "team_id", name="uq_device_team"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class DeviceTopicRow(Base):
    """A topic→device pin (execution-architecture v4 §affinity). The device a topic's
    turns run on, frozen on the first turn: the topic's work tree + resumable claude
    session live on that one machine, so every later turn MUST return to it — never
    drift to another online device (which would silently start from an empty tree and
    corrupt session resume). 1:1 — ``topic_id`` is the primary key. ``project_id`` /
    ``topics.id`` are stored as bare Uuids (no cross-table FK, matching
    ``device_project.project_id``); the device FK cascades so a removed device drops
    its pins."""

    __tablename__ = "device_topic"

    topic_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"), nullable=False, index=True
    )
