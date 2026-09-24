"""Project machines — a project's compute, provisioned from MicroCloud.

MicroCloud (the team's IaaS control plane) owns the machine itself; cheese only
remembers which machine belongs to which project, plus the tenant-side ids it
needs to talk about it again. Everything authoritative — status, IP — is
refreshed from MicroCloud on read, so this table can never be the reason cheese
shows a stale machine.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class MachineStatus(enum.StrEnum):
    """Mirrors MicroCloud's MachineStatus, plus `unknown` for when we could not
    reach it — a provider outage must not be indistinguishable from `error`."""

    provisioning = "provisioning"
    starting = "starting"
    running = "running"
    suspending = "suspending"
    suspended = "suspended"
    resuming = "resuming"
    stopping = "stopping"
    stopped = "stopped"
    deleting = "deleting"
    deleted = "deleted"
    error = "error"
    unknown = "unknown"


# The statuses a machine can still leave on its own; anything else is settled.
TRANSITIONAL = {
    MachineStatus.provisioning,
    MachineStatus.starting,
    MachineStatus.suspending,
    MachineStatus.resuming,
    MachineStatus.stopping,
    MachineStatus.deleting,
}


class AiStatus(enum.StrEnum):
    """MicroCloud's report on the machine's built-in AI channel.

    Cheese creates every machine with `aiMode: none`, which MicroCloud reports
    as `disabled`: the machine only executes tools, and its sessions' models
    come from the session host. Kept separate from MachineStatus because it is
    a separate field of MicroCloud's answer, and `error` here still means the
    provider gave up on the machine.
    """

    disabled = "disabled"
    provisioning = "provisioning"
    ready = "ready"
    error = "error"
    unknown = "unknown"


# The AI lifecycle can still move on its own here.
AI_TRANSITIONAL = {AiStatus.provisioning}

# Machines that no longer exist as far as MicroCloud is concerned. They must not
# occupy a project's slot, or deleting one and creating another is impossible.
GONE = {MachineStatus.deleted}

# Enrollment retries, then stops. A machine that cannot be enrolled is a real
# problem to look at, not something to keep SSHing at forever.
MAX_ENROLL_ATTEMPTS = 5


class WarmMachine(UuidPk, Timestamps, Base):
    """Unused platform capacity; claim intent survives a provider timeout."""

    __tablename__ = "warm_machines"

    state: Mapped[str] = mapped_column(String(16), default="preparing", index=True)
    machine_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    # Includes the public bootstrap key, never a user credential. Persist before create.
    create_request: Mapped[dict] = mapped_column(JSONB)
    bootstrap_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_machine_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_machines.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )


class ProjectMachine(UuidPk, Timestamps, Base):
    __tablename__ = "project_machines"
    __table_args__ = (
        Index(
            "uq_project_machines_active_topic",
            "topic_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND session_id IS NULL"),
        ),
        Index(
            "uq_project_machines_active_session",
            "session_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND superseded_at IS NULL"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    warm_claim_pending: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # Keep the durable lease owner even if its session is deleted: an external
    # VM must not disappear from the resource ledger through a cascading FK.
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # A migrated session no longer uses this VM, but its files and quota remain
    # until explicit cleanup. released_at would prematurely free the quota.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NULL is a manually provisioned project machine. Rows without session_id
    # retain their legacy room ownership; migration must not guess an agent.
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # MicroCloud's own ids. Kept so a later call never has to re-resolve them by
    # listing and matching on a name. NULL while the machine is being created:
    # the row is the reservation that counts against the team's quota, written
    # under the quota lock before the provider is asked (see `provision`).
    machine_id: Mapped[int | None] = mapped_column(
        BigInteger, index=True, nullable=True
    )
    customer_id: Mapped[int] = mapped_column(BigInteger)
    account_id: Mapped[int] = mapped_column(BigInteger)
    offering_id: Mapped[int] = mapped_column(BigInteger)

    hostname: Mapped[str] = mapped_column(String(64))
    login_user: Mapped[str] = mapped_column(String(32))
    cores: Mapped[int] = mapped_column(BigInteger)
    memory_mb: Mapped[int] = mapped_column(BigInteger)
    disk_gb: Mapped[int] = mapped_column(BigInteger)

    # Last known values, refreshed from MicroCloud whenever we read the machine.
    status: Mapped[MachineStatus] = mapped_column(
        Enum(MachineStatus, native_enum=False, length=16),
        default=MachineStatus.provisioning,
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # MicroCloud's built-in AI channel for the machine and how far its setup has
    # got, as MicroCloud reports them. Cheese creates every machine with
    # `aiMode: none` (models come from the session host, never the machine), so
    # these read `none` / `disabled` once settled. Free-form text for the mode:
    # it names a MicroCloud-side route, and a new one must not break reads here.
    ai_mode: Mapped[str] = mapped_column(
        String(16), default="none", server_default="none"
    )
    ai_status: Mapped[AiStatus] = mapped_column(
        Enum(AiStatus, native_enum=False, length=16),
        default=AiStatus.unknown,
    )

    # Who asked for it (a user handle), for the audit trail on a shared project.
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The user id the enrolled device is owned by — the requester. Kept because
    # enrollment happens later, in a sweep, long after the request returned.
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- enrollment: becoming an agent host cheese can run turns on ---
    # The cheese device this machine was enrolled as; None until it has been.
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why the last attempt failed. Kept so a stuck machine explains itself
    # instead of silently never appearing as compute.
    enroll_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enroll_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # A throwaway private key, authorised on the machine alongside the human's,
    # solely so the platform can perform the one-time bootstrap. Erased the
    # moment enrollment succeeds — it is a means, not an access path we keep.
    bootstrap_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When MicroCloud last answered about this machine at all. `updated_at` is
    # not a substitute: it only moves when a field actually changes, so a
    # machine reconciled repeatedly with the same answer would look permanently
    # stale and be re-fetched on every read.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
