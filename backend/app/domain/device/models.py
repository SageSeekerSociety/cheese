"""SQLAlchemy models backing the connector device flow (Act 3 PG repository).

Two tables mirror the storage-agnostic dataclasses in ``repository.py``:
``device`` (enrolled machines + their durable token) and ``device_auth_code``
(short-lived device-flow codes). The ``SqlDeviceRepository`` converts between these
rows and the dataclasses; ``DeviceService`` never sees them.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Sequence, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

device_project_seq = Sequence("device_project_seq")


class DeviceRow(Base):
    __tablename__ = "device"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The durable device token. TODO(security): store a hash and deliver the raw
    # token once via the auth code, so a DB read cannot impersonate a device.
    token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceAuthCodeRow(Base):
    __tablename__ = "device_auth_code"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending | approved
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceProjectRow(Base):
    """A device↔project assignment: which projects a device may run agents in.
    The device's owner manages these (many-to-many)."""

    __tablename__ = "device_project"
    __table_args__ = (UniqueConstraint("device_id", "project_id", name="uq_device_project"),)

    id: Mapped[int] = mapped_column(
        BigInteger, device_project_seq, primary_key=True, server_default=device_project_seq.next_value()
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
