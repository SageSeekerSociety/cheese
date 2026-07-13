"""SQLAlchemy models backing the device flow (SQL repository).

Three tables mirror the storage-agnostic dataclasses in ``repository.py``:
``device`` (enrolled compute machines + durable token), ``device_auth_code``
(short-lived device-flow codes) and ``device_project`` (device↔project assignments).
``SqlDeviceRepository`` converts between these rows and the dataclasses; the service
never sees them.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
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
