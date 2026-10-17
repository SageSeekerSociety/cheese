"""SQLAlchemy models backing the device flow (SQL repository).

Tables mirror the storage-agnostic dataclasses in ``repository.py``:
``device`` (shared connector identity), ``hosted_device`` (human-owned machines),
``device_auth_code``
(short-lived device-flow codes), ``device_project`` (device↔project assignments),
``device_team`` (device↔team bindings — compute belongs to the team, v4) and
``device_health``
(a machine's rolling failure streak / quarantine — #186) and
``device_claude_login`` (whether its owner's own Claude Code is logged in for
the platform — #2991) and ``device_sandbox`` (whether it can give a session a
sandbox). ``SqlDeviceRepository``
converts between these rows and the dataclasses; the service never sees them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps
from app.domain.device.supply import Supply


class DeviceRow(Base):
    __tablename__ = "device"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cloud_control_private: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
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
    # When the server last heard from this machine: written as it connects and
    # at most once a minute while connected; null until it first connects.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Supply is the stored lifecycle fact: who brought the machine in.
    supply: Mapped[Supply] = mapped_column(
        Enum(Supply, native_enum=False, length=16),
        default=Supply.self_hosted,
        server_default=Supply.self_hosted.value,
        nullable=False,
    )


class HostedDeviceRow(Base):
    """The human-owned, persistent subtype of a connector ``device``."""

    __tablename__ = "hosted_device"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"), primary_key=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
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


class DeviceHealthRow(Base):
    """Rolling health of one compute machine — the basis for "this body is dead".

    One row per device, written only when something goes wrong: a turn that fails
    with a HOST-SCOPED platform failure (disk full, machine unreachable) bumps
    ``consecutive_failures``; any turn that gets through deletes the row. When the
    streak reaches the threshold the device is QUARANTINED (``quarantined_until``)
    rather than destroyed — a full disk heals on its own once other topics finish,
    and destroying a machine to fix a ten-minute problem is the expensive way to be
    wrong. ``last_failure_code`` makes the streak per-cause: two different failures
    in a row are two accidents, not a dying machine.
    """

    __tablename__ = "device_health"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"), primary_key=True
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quarantined_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DeviceClaudeLoginRow(Base):
    """Whether the owner's own Claude Code on a machine is logged in for the
    platform (`cheesehost claude login`), as the machine last answered.

    One row per machine, written each time the backend asks it — when it
    connects, and before a session of the owner's Claude Code starts on it. The
    machine is the source of truth; this is what it said, and when.
    """

    __tablename__ = "device_claude_login"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"), primary_key=True
    )
    # The pinned build is on the machine. Without it nothing is logged in yet.
    installed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    logged_in: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # What `claude auth status` names: `claude.ai` for a subscription login, or
    # the API key kind, and for a subscription its plan (`max`, `pro`, …). A
    # machine whose owner set another model service is `model_service`
    # (`login_kind.MODEL_SERVICE`), with the model it calls there.
    auth_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subscription_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The host of that model service (never its key), for its owner to tell
    # one service from another; None for a Claude account.
    service_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # What its owner named this connection on 「模型连接」; None until they do.
    # The machine never reports it, so its reports leave it alone, and the own
    # agents on the connection are named after it.
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # When its owner removed this login from a browser while the machine was
    # away. Until the machine has logged it out — the next time it connects —
    # the login counts as logged out (``logged_in`` stays false whatever the
    # machine says), so nothing starts on it; None once that is done.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DeviceSandboxRow(Base):
    """Whether a machine a person enrolled can give a session a sandbox, as it
    last answered: asked when it connects and when its owner asks again
    (`device.sandbox`). The machine runs the very check its install runs, so
    the answer is the install's own; ``reason`` is the sentence it said when it
    cannot, for its owner, as ``{"key", "params"}`` JSON (`device.sandbox`).
    """

    __tablename__ = "device_sandbox"

    device_id: Mapped[str] = mapped_column(
        ForeignKey(
            "device.device_id", ondelete="CASCADE", name="fk_device_sandbox_device_id"
        ),
        primary_key=True,
    )
    isolates: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
