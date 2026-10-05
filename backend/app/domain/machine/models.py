"""The platform's cloud hosts — MicroCloud machines that carry session sandboxes.

A host belongs to the platform, never to a project, room or session: any
session that runs on cloud is placed on any host with a free slot, whichever
project it is from. MicroCloud owns the machine itself; cheese remembers the
provider ids it needs to talk about it again, and which session homes are on
it (``CloudHostHome``). Everything authoritative — status, IP — is kept in line
with MicroCloud by the pool sweep (``HostPool.refresh_due``), and reads report
what it last learned.

A session that asks for a whole machine gets a host of its own instead
(``CloudHost.whole_machine``): a whole cloud VM, its one home that session's.
"""

import enum
import uuid
from datetime import UTC, datetime, timedelta

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

from app.core.config import settings
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

    Cheese creates every host with `aiMode: none`, which MicroCloud reports as
    `disabled`: the host only executes tools, and its sessions' models come
    from the session host. Kept separate from MachineStatus because it is a
    separate field of MicroCloud's answer, and `error` here still means the
    provider gave up on the machine.
    """

    disabled = "disabled"
    provisioning = "provisioning"
    ready = "ready"
    error = "error"
    unknown = "unknown"


# The AI lifecycle can still move on its own here.
AI_TRANSITIONAL = {AiStatus.provisioning}

# Machines that no longer exist as far as MicroCloud is concerned.
GONE = {MachineStatus.deleted}

#: The agent identity every host's device belongs to. Hosts are the platform's,
#: so their devices are nobody's team's or project's.
HOST_OWNER = "cheese-host-pool"

# Enrollment retries, then stops. A host that cannot be enrolled is a real
# problem to look at, not something to keep SSHing at forever.
MAX_ENROLL_ATTEMPTS = 5

# A host the provider fails before it was ever enrolled is released and its
# waiting sessions are placed again. MicroCloud refuses quota, offering and spec
# problems at create time, so `error` is a failure while building the machine (a
# Proxmox task, SSH, init). A provider that fails every time would be asked
# forever, so the pool stops creating hosts once this many failed within the
# window.
MAX_PROVIDER_ERRORS = 3
PROVIDER_ERROR_WINDOW = timedelta(hours=1)


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
    claimed_host_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cloud_hosts.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )


class CloudHost(UuidPk, Timestamps, Base):
    __tablename__ = "cloud_hosts"

    # MicroCloud's own ids. Kept so a later call never has to re-resolve them by
    # listing and matching on a name. NULL while the machine is being created:
    # the row is the reservation the pool counts, written before the provider
    # is asked (see ``HostPool._create``).
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

    # Last known values, refreshed from MicroCloud by the pool sweep.
    status: Mapped[MachineStatus] = mapped_column(
        Enum(MachineStatus, native_enum=False, length=16),
        default=MachineStatus.provisioning,
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # MicroCloud's built-in AI channel for the machine and how far its setup has
    # got. Cheese creates every host with `aiMode: none`, so these read `none` /
    # `disabled` once settled. Free-form text for the mode: it names a
    # MicroCloud-side route, and a new one must not break reads here.
    ai_mode: Mapped[str] = mapped_column(
        String(16), default="none", server_default="none"
    )
    ai_status: Mapped[AiStatus] = mapped_column(
        Enum(AiStatus, native_enum=False, length=16),
        default=AiStatus.unknown,
    )
    # A warm machine reserved for this host whose provider claim has not been
    # recorded yet (``WarmPoolService.finish_claim``).
    warm_claim_pending: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )

    # --- enrollment: becoming a device cheese can run sandboxes on ---
    device_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why the last attempt failed. Kept so a stuck host explains itself.
    enroll_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enroll_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # A throwaway private key, authorised on the machine alongside the
    # operator's, solely so the platform can perform the one-time bootstrap.
    # Erased the moment enrollment succeeds.
    bootstrap_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When MicroCloud last answered about this machine at all. `updated_at` only
    # moves when a field changes, so a host reconciled repeatedly with the same
    # answer would look permanently stale.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- the pool ---
    # A host that keeps the sessions already on it and is given no new ones,
    # and is released once no home is left on it. A host idle long enough is
    # set draining while its sleeping homes are archived. Hosts that each
    # session or room rented for itself, before the pool, were adopted this
    # way: they were enrolled without the session sandbox.
    draining: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # Since when the host has run no sandbox; NULL while it runs one. Once this
    # is older than ``cloud_host_idle_hold_s`` its sleeping homes are archived
    # (it is set draining for that) and, with none left, it is released.
    idle_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The provider failed it before it was enrolled. Counted against
    # ``MAX_PROVIDER_ERRORS``; such a host is released at once.
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Taken out of the pool: no placement sees it, and its provider delete is
    # under way or retried by the sweep until MicroCloud confirms it.
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # A whole cloud VM for one session, not a host of sandboxes: created for
    # that session from ``microcloud_vm_offering_id``, its executor runs with
    # the whole machine (``host`` visibility, sudo), it takes no other session
    # and is released as soon as its session's home is gone. Never warm.
    whole_machine: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # Whose session a whole VM was created for: what billing charges its
    # spec × time to (#2320 step 4). NULL on a pool host, which is no
    # project's cost.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )


class CloudHostHome(UuidPk, Timestamps, Base):
    """One session's home: its sandbox's directory, on a host or archived.

    Written when the session is placed, before anything is on the disk, so a
    session waiting for its host keeps its place. While its sandbox runs it
    holds one of the host's slots; asleep (``stopped_at``) it holds only the
    host's disk. Archived (``host_id`` NULL, ``archive_key`` set) it is on no
    host at all: the session's next tool call places it again and restores it.

    Deleted when the session's work there has been pushed and it moved on, or
    when its room's cleanup has removed the directory (or the archive). A
    session that left without pushing keeps its home (``left_at``): its work is
    only there, and the home is not deleted until the room's cleanup.
    """

    __tablename__ = "cloud_host_homes"
    __table_args__ = (
        Index(
            "uq_cloud_host_homes_current_session",
            "session_id",
            unique=True,
            postgresql_where=text("left_at IS NULL AND session_id IS NOT NULL"),
        ),
    )

    # NULL while the home is archived and on no host.
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cloud_hosts.id", ondelete="CASCADE"), index=True, nullable=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # The room generation the home belongs to (``topic.resource_id``): a room's
    # cleanup removes the homes of the generation it was opened for.
    room_resource_id: Mapped[str] = mapped_column(String(36))
    # The directory's resource on the host (the lease's ``resource_id``).
    resource_id: Mapped[str] = mapped_column(String(36))
    # NULL only for a room's directory from before session leases.
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # The session moved on without pushing what it did here.
    left_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the room was told this session's sandbox is being prepared; cleared
    # once it is told the sandbox is ready.
    waiting_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The session's last tool call here (moved at most once a minute); with the
    # room's turns, what says whether the sandbox is idle.
    active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # The sandbox was stopped; its home is on disk (or archived) and the next
    # tool call starts it. NULL while it runs, or may run.
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Something is stopping, archiving or restoring the home until then; nothing
    # else may start or move it meanwhile. A sweep that dies lets it lapse.
    busy_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The archive in the private bucket, as verified when it was written: the
    # object's key, its size and its bytes' MD5. Set while the home is archived
    # and until it has been restored.
    archive_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    archive_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    archive_md5: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Whether the host found everything in the home on its remote when it wrote
    # the archive. The room's cleanup deletes an archive only when this is
    # true: one that holds unpushed work keeps the cleanup waiting, and
    # unarchiving the room brings the home back from it. NULL — an archive
    # written before the host was asked — counts as not pushed.
    archive_published: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # The last archive of this home failed, and why; it is not tried again
    # until ``lifecycle.ARCHIVE_RETRY`` has passed. Cleared by one that works.
    archive_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archive_error: Mapped[str | None] = mapped_column(Text, nullable=True)


def capacity(host: CloudHost) -> int:
    """The sandboxes a host runs at once: per core, by the deployment's
    setting. A whole cloud VM has none to give: it is its one session's."""
    if host.whole_machine:
        return 0
    return max(1, int(host.cores)) * settings.cloud_host_slots_per_core


def disk_capacity(host: CloudHost) -> int:
    """The homes a host keeps on its disk, running or asleep: its disk at
    ``cloud_sandbox_disk_gb`` each, and never fewer than it runs at once. A
    whole cloud VM keeps none but its own session's."""
    if host.whole_machine:
        return 0
    return max(capacity(host), int(host.disk_gb) // settings.cloud_sandbox_disk_gb)
