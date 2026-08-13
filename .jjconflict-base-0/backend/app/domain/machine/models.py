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

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class MachineStatus(enum.StrEnum):
    """Mirrors MicroCloud's MachineStatus, plus `unknown` for when we could not
    reach it — a provider outage must not be indistinguishable from `error`."""

    provisioning = "provisioning"
    starting = "starting"
    running = "running"
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
    MachineStatus.stopping,
    MachineStatus.deleting,
}


class AiStatus(enum.StrEnum):
    """How far MicroCloud has got wiring this machine's Claude Code up.

    Deliberately separate from MachineStatus: a machine reports `running` while
    its agent access is still being provisioned, so treating the two as one
    would call a machine usable before it can run a turn.
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


class ProjectMachine(UuidPk, Timestamps, Base):
    __tablename__ = "project_machines"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # MicroCloud's own ids. Kept so a later call never has to re-resolve them by
    # listing and matching on a name.
    machine_id: Mapped[int] = mapped_column(BigInteger, index=True)
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
    # How the machine's Claude Code reaches a model (none | newapi | ccproxy),
    # and how far that setup has got. Free-form text for the mode: it names a
    # MicroCloud-side supply route, and a new one must not break reads here.
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
