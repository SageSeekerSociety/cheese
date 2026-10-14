"""Keep the disk of a machine that runs sandboxes from filling up.

A sandbox's home — its checkout, its dependencies, its /tmp — is on the disk
of the machine it runs on, shared with every other sandbox there and, on a
person's computer, with everything of theirs. Nothing inside a sandbox bounds
what it writes: a platform host is an unprivileged container, with no loop
device to give each sandbox a filesystem of its own and no project quotas. So
this is held the way Kubernetes holds a node's ephemeral storage: by reading
what is used and stopping sandboxes, not by a limit in the filesystem.

Every ``sandbox_disk_check_s`` (a minute), each online machine holding a
sandbox is read (``sandbox_home.disk``): the free bytes and inodes of the
filesystem its homes are on, which costs one ``statvfs``. What each home holds
takes a walk of every file in it — about 13 s on a dev host holding 880
thousand inodes — so that is read only every ``sandbox_disk_walk_s`` (five
minutes), and at once on any check that finds the disk below a floor.

- Below ``sandbox_disk_free_floor`` of its bytes or ``sandbox_inodes_free_floor``
  of its inodes, a platform host takes no new sandbox
  (``CloudHost.disk_full_since``, ``services.accepting``) until a reading finds
  room again, and the machine's heaviest sandbox — the most of either, as a
  share of the filesystem — is destroyed and its room told what it held and
  where. One a reading: the next shows whether that was enough.
- On a platform host, a sandbox holding more than one sandbox may
  (``cloud_sandbox_disk_budget_gb``, ``cloud_sandbox_inode_budget``) is
  destroyed on the next walk, whatever room the rest of the disk has, so one
  sandbox cannot fill a host for the others; its room is told the same, with
  the limit. A person's computer has no such budget: its disk is theirs to
  fill.
- A sandbox's home whose sandbox no longer exists — its row went when its
  session moved away (``HostPool.leave``) — is deleted. A sandbox is
  destroyed with its home, so no home is meant to outlive its row; one that
  does holds a disk nobody will free.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.agent.device_hub import device_hub
from app.domain.machine import lifecycle, sandbox_home
from app.domain.machine.lifecycle import STOP_HOLD, SandboxLifecycle
from app.domain.machine.models import CloudHost, SandboxHome
from app.domain.machine.progress import tell_disk_full, tell_over_budget
from app.domain.machine.repositories import CloudHostRepository

logger = logging.getLogger("cheese.machine.disk_guard")

#: A home with no sandbox is deleted only once nothing has written it for this
#: long. A sandbox's row is written before its home is made, so a home without
#: one that is this old is not one being placed right now.
LEFT_BEHIND_AFTER = timedelta(minutes=10)
#: A sandbox is evicted only if it holds at least this share of the machine's
#: bytes or inodes. A person's disk may be full of their own files, and
#: stopping a sandbox that holds little of it frees nothing.
EVICT_MIN_SHARE = 0.01
#: The longest one machine's reading may take: its walk stops at ``WALK_S``.
READ_TIMEOUT_S = sandbox_home.WALK_S + 60
#: Where a sandbox sees its temporary directory, inside its home.
SANDBOX_TMP = ".cheese/tmp"

# When each machine's homes were last walked. In this process: the jobs run in
# the one that holds the work, and one that takes it over walks each machine on
# its first check.
_walked: dict[str, datetime] = {}


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


def share(home: dict, filesystem: dict) -> float:
    """How much of the filesystem a home holds: the larger of its share of the
    bytes and of the inodes."""
    of_bytes = home["bytes"] / filesystem["bytes"] if filesystem["bytes"] else 0
    inodes = filesystem.get("inodes") or 0
    return max(of_bytes, home["inodes"] / inodes if inodes else 0)


def over_budget(home: dict) -> bool:
    """Whether a home on a platform host holds more than one sandbox may."""
    return (
        home["bytes"] > settings.cloud_sandbox_disk_budget_gb * 10**9
        or home["inodes"] > settings.cloud_sandbox_inode_budget
    )


def held(home: dict) -> dict[str, str]:
    """What a home held, as its room is told it."""
    return {
        "gb": f"{home['bytes'] / 1e9:.1f}",
        "files": f"{home['inodes']:,}",
        "where": where(home["top"]),
    }


def where(top: str) -> str:
    """The directory a reading found most of a home under, as the sandbox
    sees it: its home is ``~`` and its temporary directory ``/tmp``."""
    if top == SANDBOX_TMP or top.startswith(SANDBOX_TMP + "/"):
        return "/tmp" + top[len(SANDBOX_TMP) :]
    return "~/" + top if top else "~"


class DiskGuard:
    def __init__(self, session: AsyncSession, hub=None) -> None:
        self._session = session
        self._hub = hub or device_hub
        self._repo = CloudHostRepository(session)
        self._lifecycle = SandboxLifecycle(session, hub=self._hub)

    async def sweep(self) -> dict[str, int]:
        counts = {"read": 0, "walked": 0, "evicted": 0, "removed": 0}
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
        """Every machine holding a sandbox, and every platform host still
        marked full: a host whose last sandbox went is read once more to take
        the mark off."""
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
        marked = await self._session.scalars(
            select(CloudHost.device_id).where(
                CloudHost.disk_full_since.is_not(None),
                CloudHost.device_id.is_not(None),
                CloudHost.released_at.is_(None),
            )
        )
        machines = sorted({device for device in (*holding, *marked) if device})
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
            )
            .outerjoin(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(device == device_id)
        )
        live = list(rows.all())
        await self._session.commit()
        return live

    async def check(self, device_id: str) -> dict[str, int]:
        """Read one machine and act on what it shows: its free space every
        time, what its homes hold when a walk is due or the disk is short. A
        sandbox's budget is judged on a walk, the only reading that has it."""
        live = await self._live(device_id)
        now = datetime.now(UTC)
        last = _walked.get(device_id)
        walk = last is None or now - last >= timedelta(
            seconds=settings.sandbox_disk_walk_s
        )
        reading = await self._read(device_id, live, walk=walk)
        filesystem = reading["filesystem"]
        full = below_floor(filesystem)
        if full and not walk:
            # Short of disk: which sandbox to stop is decided on what the homes
            # hold now, not five minutes ago.
            walk = True
            reading = await self._read(device_id, live, walk=True)
            filesystem = reading["filesystem"]
            full = below_floor(filesystem)
        if walk:
            _walked[device_id] = now
        await self._mark(device_id, full, filesystem)
        host = await self._repo.by_device(device_id)
        await self._session.commit()
        running = {
            (str(row.project_id), row.resource_id): row
            for row in live
            if row.stopped_at is None
        }
        # Empty unless this reading walked the homes.
        homes = [
            (home, running[key])
            for home in reading["homes"]
            if (key := (home["project"], home["resource"])) in running
        ]
        evicted = 0
        if host is not None:
            limit = {
                "limit_gb": str(settings.cloud_sandbox_disk_budget_gb),
                "limit_files": f"{settings.cloud_sandbox_inode_budget:,}",
            }
            for home, row in list(homes):
                if over_budget(home) and await self._evict(
                    device_id,
                    row,
                    home,
                    partial(tell_over_budget, **held(home), **limit),
                ):
                    evicted += 1
                    homes.remove((home, row))
        # After an eviction this reading is out of date: the next one shows
        # whether the disk still needs another.
        if (
            full
            and not evicted
            and await self._evict_heaviest(device_id, filesystem, homes)
        ):
            evicted += 1
        removed = 0
        for home in reading["left_behind"]:
            if await self._remove_left_behind(
                device_id, home["project"], home["resource"]
            ):
                removed += 1
        return {"read": 1, "walked": int(walk), "evicted": evicted, "removed": removed}

    async def _read(self, device_id: str, live: list, *, walk: bool) -> dict:
        return await lifecycle.run_on_host(
            self._hub,
            device_id,
            "disk",
            timeout=READ_TIMEOUT_S,
            live=[[str(row.project_id), row.resource_id] for row in live],
            grace_s=LEFT_BEHIND_AFTER.total_seconds(),
            walk=walk,
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

    async def _evict_heaviest(
        self, device_id: str, filesystem: dict, homes: list[tuple[dict, object]]
    ) -> bool:
        if not homes:
            return False
        home, row = max(homes, key=lambda item: share(item[0], filesystem))
        if share(home, filesystem) < EVICT_MIN_SHARE:
            logger.warning(
                "disk of %s is short, but no sandbox on it holds %s of it",
                device_id,
                f"{EVICT_MIN_SHARE:.0%}",
            )
            return False
        return await self._evict(
            device_id, row, home, partial(tell_disk_full, **held(home))
        )

    async def _evict(self, device_id: str, row, home: dict, tell) -> bool:
        evicted = await self._lifecycle.evict(
            row.id, row.topic_id if row.session_id else None, device_id, tell
        )
        if evicted:
            logger.warning(
                "evicted sandbox %s from %s: it held %s bytes and %s inodes",
                row.id,
                device_id,
                home["bytes"],
                home["inodes"],
            )
        return evicted

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
