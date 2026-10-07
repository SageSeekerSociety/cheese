"""Waking a pool host MicroCloud has suspended or stopped, instead of giving
it up for its connector being away (``services.HostPool.maintain``).

A suspended or stopped machine still has its disk, and with it whatever its
sessions did not push; on 2026-10-06 two suspended dev hosts were deleted for
their connectors being away. So before the pool gives a host up it asks
MicroCloud about the machine, and wakes it when MicroCloud keeps it parked.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerting
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError
from app.domain.machine.models import GONE, CloudHost, MachineStatus
from app.domain.machine.repositories import CloudHostRepository

logger = logging.getLogger("cheese.machine")

#: How long the pool keeps waking a host MicroCloud has suspended or stopped
#: before it stops and keeps the host for a person. MicroCloud brings one back
#: in seconds (dev, 2026-09-22: an LXC host started on its disks in 3.6 s, a VM
#: resumed from hibernation in 8.1 s), and a booted host's connector dials in
#: within ``services.CONNECT_GRACE`` (five minutes); twice that is past any
#: wake that is going to work.
WAKE_WITHIN = timedelta(minutes=10)
#: Resume or start requests one wake sends. An accepted one moves the machine
#: on (``resuming``, ``starting``); another is sent only when MicroCloud reports
#: it suspended or stopped again, the last having failed — as one resume on dev
#: did within two seconds (2026-09-22), where a later one on the same machine
#: worked.
MAX_WAKE_REQUESTS = 3
#: What MicroCloud reports of a machine that is not running but keeps its disk:
#: a host in one of these is woken, never given up (``HostWake.away``).
PARKED = {
    MachineStatus.suspending,
    MachineStatus.suspended,
    MachineStatus.resuming,
    MachineStatus.stopping,
    MachineStatus.stopped,
    MachineStatus.starting,
}


def waking_or_kept(host: CloudHost) -> bool:
    """The pool is waking the host, or kept it once the wake failed."""
    return host.waking_since is not None or host.wake_failed_at is not None


def _as_status(value: object) -> MachineStatus:
    """A status this client does not know reads as ``unknown``."""
    try:
        return MachineStatus(str(value))
    except ValueError:
        return MachineStatus.unknown


class HostWake:
    def __init__(
        self,
        session: AsyncSession,
        repo: CloudHostRepository,
        client: MicroCloudClient,
    ) -> None:
        self._session = session
        self._repo = repo
        self._client = client

    async def away(self, host: CloudHost, give_up) -> None:
        """Decide about an enrolled host whose connector is away, before
        giving it up (``give_up``), by asking MicroCloud what it has now. Asked
        with no lock held.

        A machine MicroCloud no longer has (404), one it reports broken, and
        one it reports running while its connector stays away are given up:
        a sandbox is disposable. One it keeps suspended or stopped still has
        its disk, and with it whatever its sessions did not push: it is woken
        through MicroCloud and keeps everything on it; once its connector is
        back it is a host like any other, and a draining one archives its homes
        and goes. A wake that does not bring it back within ``WAKE_WITHIN`` and
        ``MAX_WAKE_REQUESTS``, or that MicroCloud reports failed (``error``),
        is stopped: the host is kept with its data, off the pool's count, and a
        person is alerted. The pool never deletes it. When MicroCloud does not
        answer, a lost host cannot be told from a parked one, and nothing is
        decided this round."""
        machine_id = host.machine_id
        if machine_id is None:
            # Never created at MicroCloud: nothing there to wake.
            await give_up(host)
            return
        try:
            remote = await self._client.get_machine(machine_id)
        except MicroCloudError as exc:
            logger.warning(
                "cloud pool cannot reach MicroCloud about host %s, keeping it "
                "this round: %s",
                host.hostname,
                exc,
            )
            return
        if remote is None:
            # MicroCloud no longer has it (404): nothing is left to keep.
            await give_up(host)
            return
        status = _as_status(remote.get("status"))
        if host.waking_since is None:
            if status == MachineStatus.unknown:
                logger.warning(
                    "cloud pool keeps host %s this round: MicroCloud reports %r",
                    host.hostname,
                    remote.get("status"),
                )
                return
            if status not in PARKED:
                await give_up(host)
                return
        elif status in GONE or status == MachineStatus.deleting:
            # Deleted at MicroCloud while being woken: nothing is left to keep.
            await give_up(host)
            return
        await self._wake(host, machine_id, status)

    async def _wake(
        self, host: CloudHost, machine_id: int, status: MachineStatus
    ) -> None:
        """One sweep's step of waking a host (``away``): ask MicroCloud to
        resume or start it while it reports it suspended or stopped, and stop
        once the wake is out of time or requests, or failed."""
        now = datetime.now(UTC)
        since = host.waking_since or now
        requests = host.wake_requests if host.waking_since is not None else 0
        answer = None
        failed = None
        if status == MachineStatus.error:
            failed = "MicroCloud reports the machine in error while waking it"
        elif now - since >= WAKE_WITHIN:
            failed = (
                f"its connector did not come back within {WAKE_WITHIN}; "
                f"MicroCloud reports {status}"
            )
        elif status in (MachineStatus.suspended, MachineStatus.stopped):
            if requests >= MAX_WAKE_REQUESTS:
                failed = (
                    f"MicroCloud still reports {status} after {requests} "
                    "resume/start requests"
                )
            else:
                wake = (
                    self._client.resume_machine
                    if status == MachineStatus.suspended
                    else self._client.start_machine
                )
                requests += 1
                try:
                    answer = await wake(machine_id)
                except MicroCloudError as exc:
                    answer = str(exc)
                    if requests >= MAX_WAKE_REQUESTS:
                        failed = (
                            f"MicroCloud refused request {requests} to wake it "
                            f"from {status}: {exc}"
                        )
                logger.info(
                    "cloud pool asked MicroCloud to wake host %s from %s "
                    "(request %s): %s",
                    host.hostname,
                    status,
                    requests,
                    answer,
                )
        await self._repo.lock_pool()
        await self._session.refresh(host)
        if host.released_at is not None or host.offline_since is None:
            # Given up elsewhere, or back meanwhile.
            await self._session.commit()
            return
        host.waking_since = since
        host.wake_requests = requests
        if failed is not None:
            host.wake_failed_at = now
        await self._session.commit()
        if failed is not None:
            self._alert(host, failed)

    def _alert(self, host: CloudHost, why: str) -> None:
        logger.warning(
            "cloud pool stopped waking host %s (machine %s), kept: %s",
            host.hostname,
            host.machine_id,
            why,
        )
        alerting.send(
            "云主机没能唤醒，已保留",
            [
                f"主机：{host.hostname}（MicroCloud 机器 {host.machine_id}）",
                f"原因：{why}",
                "这台主机和上面的沙箱都还在，平台不会自动删除它，"
                "它也不再占池子的主机数。它的连接器回来后会自动回到池里；"
                "确认不要了，就在 MicroCloud 删除这台机器，池子随后会清掉记录。",
            ],
            key=f"cloud-host-unwoken:{host.id}",
        )
