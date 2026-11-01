"""Keep the disk of a machine that runs sandboxes from filling up, and have
idle sandboxes give back their memory.

A platform host keeps its sandboxes' homes on a data disk of its own, where
each home, its executor's state and its project's package store have a
project quota (``sandbox_host.py``): one sandbox cannot hold more than
``cloud_sandbox_disk_budget_gb`` and ``cloud_sandbox_inode_budget`` however
much room the rest of the disk has, and it is the one whose writes are
refused when it tries. The limits of every sandbox on a host can add up to
more than its disk — a sandbox is expected to hold far less than its limit
(``cloud_sandbox_disk_gb``, ``models.capacity``) — so the disk as a whole is
still read.

Every ``sandbox_disk_check_s`` (a minute), each online machine holding a
sandbox is read (``sandbox_home.disk``): the free bytes and inodes of the
filesystem its homes are on, and on a platform host what each home holds by
its quota, a ``statvfs`` each.

- Below ``sandbox_disk_free_floor`` of its bytes or ``sandbox_inodes_free_floor``
  of its inodes, on its data disk or on its own disk, where its executor
  releases are staged, a platform host takes no new sandbox
  (``CloudHost.disk_full_since``, ``reaper.accepting``) until a reading finds
  room again. Nothing on it is stopped: each sandbox is within its own limit,
  and the host's sandboxes end, and free their room, as they go idle.
- A sandbox's home whose sandbox no longer exists — its row went when its
  session moved away (``HostPool.leave``) — is deleted. A sandbox is
  destroyed with its home, so no home is meant to outlive its row; one that
  does holds a disk nobody will free.
- A sandbox whose session has asked for no tool for ``RECLAIM_AFTER`` gives
  back its page cache (``sandbox_host.py reclaim``): the memory a VM host's
  guest kernel frees is reported to the hypervisor, which takes it back
  (measured on a dev VM, 2026-10-20: 5.8 GiB of a sandbox's cache down to
  1.4 GiB within 25 s), so a sandbox that only waits holds no more of the
  cluster than it needs. A person's computer has no helper and skips this.

Every platform host is read, those with no sandbox too, and what a host's
reading found — its disk, memory and CPU, and each sandbox's memory and disk —
is kept for the admin machines page (``readings``), which also tells the alert
group of a host running short. A person's computer's reading is not kept.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.agent.device_hub import device_hub
from app.domain.machine import holder as holders
from app.domain.machine import lifecycle, readings
from app.domain.machine.lifecycle import STOP_HOLD
from app.domain.machine.models import CloudHost, SandboxHome
from app.domain.machine.repositories import CloudHostRepository

logger = logging.getLogger("cheese.machine.disk_guard")

#: A home with no sandbox is deleted only once nothing has written it for this
#: long. A sandbox's row is written before its home is made, so a home without
#: one that is this old is not one being placed right now.
LEFT_BEHIND_AFTER = timedelta(minutes=10)
#: The longest one machine's reading may take.
READ_TIMEOUT_S = 60
#: How long a session asks for no tool before its sandbox gives back its page
#: cache. Not at once: a session between two tool calls of a turn is still
#: reading the files it works on, and dropping them costs reading them again.
RECLAIM_AFTER = timedelta(minutes=2)


async def sweep(sessions: SessionFactory) -> dict[str, int]:
    async with sessions() as session:
        return await DiskGuard(session).sweep()


def below_floor(filesystem: dict) -> bool:
    """Whether a filesystem has less free than a machine running sandboxes
    keeps, in bytes or in inodes. One that counts no inodes has no such
    floor."""
    if (
        filesystem["bytes_free"]
        < filesystem["bytes"] * settings.sandbox_disk_free_floor
    ):
        return True
    inodes = filesystem.get("inodes") or 0
    return (
        inodes > 0
        and filesystem["inodes_free"] < inodes * settings.sandbox_inodes_free_floor
    )


class DiskGuard:
    def __init__(self, session: AsyncSession, hub=None) -> None:
        self._session = session
        self._hub = hub or device_hub
        self._repo = CloudHostRepository(session)

    async def sweep(self) -> dict[str, int]:
        counts = {"read": 0, "reclaimed": 0, "removed": 0}
        for device_id in await self._machines():
            if not self._hub.is_online(device_id):
                continue
            try:
                result = await self.check(device_id)
            except (RuntimeError, TimeoutError, KeyError, ValueError):
                logger.warning(
                    "reading the disk of %s failed", device_id, exc_info=True
                )
                continue
            for key, value in result.items():
                counts[key] += value
        return counts

    async def _machines(self) -> list[str]:
        """Every machine holding a sandbox, and every platform host in the
        pool, holding one or not: its reading is what the machines page shows,
        and one still marked full has the mark taken off by it."""
        device = func.coalesce(CloudHost.device_id, SandboxHome.device_id)
        holding = await self._session.scalars(
            select(device)
            .select_from(SandboxHome)
            .outerjoin(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(
                device.is_not(None),
                or_(
                    SandboxHome.host_id.is_(None),
                    CloudHost.released_at.is_(None)
                    # A session's whole cloud VM is its own; nothing to share.
                    & CloudHost.whole_machine.is_(False),
                ),
            )
            .distinct()
        )
        pool = await self._session.scalars(
            select(CloudHost.device_id).where(
                CloudHost.device_id.is_not(None),
                CloudHost.released_at.is_(None),
                CloudHost.whole_machine.is_(False)
                | CloudHost.disk_full_since.is_not(None),
            )
        )
        machines = sorted({device for device in (*holding, *pool) if device})
        await self._session.commit()
        return machines

    async def _live(self, device_id: str) -> list:
        device = func.coalesce(CloudHost.device_id, SandboxHome.device_id)
        rows = await self._session.execute(
            select(
                SandboxHome.id,
                SandboxHome.project_id,
                SandboxHome.resource_id,
                SandboxHome.session_id,
                SandboxHome.topic_id,
                SandboxHome.stopped_at,
                SandboxHome.active_at,
                SandboxHome.frozen_at,
            )
            .outerjoin(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(device == device_id)
        )
        live = list(rows.all())
        await self._session.commit()
        return live

    async def check(self, device_id: str) -> dict[str, int]:
        """Read one machine and act on what it shows."""
        live = await self._live(device_id)
        now = datetime.now(UTC)
        idle = [
            row.resource_id
            for row in live
            if row.stopped_at is None
            and row.frozen_at is None
            and now - row.active_at >= RECLAIM_AFTER
        ]
        reading = await self._read(device_id, live, idle)
        # When the values were true, for the machines page.
        read_at = datetime.now(UTC)
        if reading.get("reclaim_error"):
            logger.warning(
                "idle sandboxes on %s could not give back their memory: %s",
                device_id,
                reading["reclaim_error"],
            )
        filesystem = reading["filesystem"]
        system = reading.get("system")
        full = below_floor(filesystem) or (system is not None and below_floor(system))
        read = {"homes": filesystem, **({"system": system} if system else {})}
        await self._mark(device_id, full, read)
        host = await self._repo.by_device(device_id)
        await self._session.commit()
        removed = 0
        if full and host is not None and not host.whole_machine:
            # Short of room: the frozen sandboxes go first, oldest frozen
            # first. They are not in use; an active sandbox waits for its idle
            # sweep as it always did.
            removed += await lifecycle.SandboxLifecycle(
                self._session, hub=self._hub
            ).destroy_frozen_on(device_id, host_id=host.id)
        for home in reading["left_behind"]:
            if await self._remove_left_behind(
                device_id, home["project"], home["resource"]
            ):
                removed += 1
        if host is not None and not host.whole_machine:
            try:
                await readings.record(self._session, host, reading, live, read_at)
            except SQLAlchemyError:
                # The machines page and the capacity alert go without this
                # reading; that is an error a person should hear of, and still
                # not one that stops the guard from reading the next machine.
                await self._session.rollback()
                logger.exception("keeping the reading of %s failed", device_id)
        return {
            "read": 1,
            "reclaimed": len(reading.get("reclaimed") or {}),
            "removed": removed,
        }

    async def _read(self, device_id: str, live: list, idle: list[str]) -> dict:
        return await lifecycle.run_on_host(
            self._hub,
            device_id,
            "disk",
            timeout=READ_TIMEOUT_S,
            live=[[str(holders.namespace_of(row)), row.resource_id] for row in live],
            grace_s=LEFT_BEHIND_AFTER.total_seconds(),
            idle=idle,
        )

    async def _mark(self, device_id: str, full: bool, filesystem: dict) -> None:
        host = await self._repo.by_device(device_id)
        if host is None or (host.disk_full_since is not None) == full:
            await self._session.commit()
            return
        host.disk_full_since = datetime.now(UTC) if full else None
        await self._session.commit()
        logger.warning(
            "host %s %s: %s",
            host.id,
            "is short of disk and takes no new sandbox" if full else "has disk again",
            filesystem,
        )

    async def _remove_left_behind(
        self, device_id: str, project: str, resource: str
    ) -> bool:
        """Delete a sandbox's home that no sandbox has any more. Asked again
        just before: one placed since the reading keeps its home."""
        device = func.coalesce(CloudHost.device_id, SandboxHome.device_id)
        placed = await self._session.scalar(
            select(SandboxHome.id)
            .outerjoin(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(
                device == device_id,
                SandboxHome.project_id == uuid.UUID(project),
                SandboxHome.resource_id == resource,
            )
        )
        await self._session.commit()
        if placed is not None:
            return False
        try:
            await lifecycle.run_on_host(
                self._hub,
                device_id,
                "destroy",
                timeout=STOP_HOLD.total_seconds() - 30,
                project=project,
                resource=resource,
            )
        except (RuntimeError, TimeoutError):
            logger.warning(
                "removing the home %s/%s left on %s failed",
                project,
                resource,
                device_id,
                exc_info=True,
            )
            return False
        logger.info("removed the home %s/%s left on %s", project, resource, device_id)
        return True
