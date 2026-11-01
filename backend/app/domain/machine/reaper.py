"""The pool's background sweep: what ``HostPool`` does with a host it is not
placing a session on.

Taken out of ``services.py`` (#P03): placement decides which host a session
gets, and the sweep keeps the hosts themselves in line with the provider —
reads MicroCloud's own view of them, gives up on the ones that stopped
answering or that the provider failed, releases the idle ones, settles
reservations a dead backend left behind, and lets go of machines that are
gone. It runs on the pool's timer (``runner.CloudPoolSweeper``), never on a
session's tool call.

``PoolSweep`` is mixed into ``HostPool`` rather than holding one: the sweep
reads and writes the pool's own session, repository and provider client, and a
second object holding them would only be a second place for them to drift.
It is a lower module than ``services`` — ``HostPool`` imports this, this never
imports the pool — so the host predicates the two share (``free_slots``,
``accepting``, ``_as_status`` and the rest) live here and ``services`` reads
them back.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent_session.models import LOST_KEY, AgentSession
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply
from app.domain.machine import enrollment
from app.domain.machine.infra_records import keep_host_lost
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError
from app.domain.machine.models import (
    AI_TRANSITIONAL,
    GONE,
    MAX_ENROLL_ATTEMPTS,
    MAX_PROVIDER_ERRORS,
    PROVIDER_ERROR_WINDOW,
    PROVIDER_PROBE_INTERVAL,
    TRANSITIONAL,
    AiStatus,
    CloudHost,
    MachineStatus,
    capacity,
)
from app.domain.machine.progress import (
    publish_line,
    tell_lost,
    tell_ready,
    tell_replaced,
)
from app.domain.machine.repositories import CloudHostRepository

logger = logging.getLogger("cheese.machine")

#: How long an enrolled host's connector has to reach the platform before the
#: pool gives up on it, when no session has started working there yet.
CONNECT_GRACE = timedelta(minutes=5)
#: How long an enrolled pool host's connector may stay away before the pool
#: gives up on it and its sessions get new sandboxes. A dropped link is back
#: within seconds (``device_hub.RECONNECT_GRACE_S``) and a rebooted host dials
#: in within ``CONNECT_GRACE``; twice that is past any host that is coming
#: back, and still minutes, where a hung host kept its sessions waiting for
#: hours (dev, 2026-10-05: four to five).
LOST_AFTER = timedelta(minutes=10)


class CloudKeepsFailing(Exception):
    """The provider failed every host the pool asked for lately; the message is
    what the session is told instead of a sandbox (or its VM)."""

    def __init__(self, failures: int, *, whole_machine: bool = False) -> None:
        minutes = int(PROVIDER_ERROR_WINDOW.total_seconds() // 60)
        probe = int(PROVIDER_PROBE_INTERVAL.total_seconds() // 60)
        key = "cloudVmKeepsFailing" if whole_machine else "cloudKeepsFailing"
        super().__init__(say(key, failures=failures, minutes=minutes, probe=probe))


class CloudPoolFull(Exception):
    """The pool holds ``cloud_pool_max_hosts`` and none has a free slot (or a
    whole VM was asked for, which needs a host of its own)."""

    def __init__(self, *, whole_machine: bool = False) -> None:
        super().__init__(say("cloudVmBusy" if whole_machine else "cloudBusy"))


def free_slots(host: CloudHost, load: dict[uuid.UUID, int]) -> int:
    """How many more sandboxes a host can be given."""
    return max(0, capacity(host) - load.get(host.id, 0))


def accepting(host: CloudHost) -> bool:
    """Whether a new session may be placed on this host. A host being rebuilt
    takes none: its sandboxes are the ones it already has, and it is released
    once they are gone."""
    return (
        host.released_at is None
        and host.draining_since is None
        and host.failed_at is None
        and not host.whole_machine
        and host.disk_full_since is None
        and host.status not in {MachineStatus.error, MachineStatus.deleting, *GONE}
        and not _failed_unenrolled(host)
    )


def _counts_toward_cap(host: CloudHost) -> bool:
    return host.released_at is None


def _failed_unenrolled(host: CloudHost) -> bool:
    """The provider failed the host, or enrolling it kept failing, before
    anyone worked on it."""
    return host.device_id is None and (
        host.status == MachineStatus.error
        or (host.enroll_attempts or 0) >= MAX_ENROLL_ATTEMPTS
    )


def _still_moving(host: CloudHost) -> bool:
    """Whether either lifecycle can still change on its own. Both count: a
    sandbox waits on both, and MicroCloud can report them settling at
    different moments."""
    return (
        host.status in TRANSITIONAL
        or host.status == MachineStatus.unknown
        or host.ai_status in AI_TRANSITIONAL
        or host.ai_status == AiStatus.unknown
    )


def _as_status(value: object) -> MachineStatus:
    """MicroCloud is the source of truth for status, but an unrecognised value
    must not blow up a read — a newer provider status maps to `unknown`."""
    try:
        return MachineStatus(str(value))
    except ValueError:
        return MachineStatus.unknown


def _as_ai_status(value: object) -> AiStatus:
    """Same tolerance as _as_status for MicroCloud's AI state."""
    if value is None:
        return AiStatus.unknown
    try:
        return AiStatus(str(value))
    except ValueError:
        return AiStatus.unknown


class _Hub(Protocol):
    """Typing-only stand-in for the device hub: the sweep asks it whether a
    host's connector is up, the pool whether one is on its way back. Declared
    rather than imported so this module stays below ``agent`` in the domain
    layering."""

    def is_online(self, device_id: str) -> bool: ...
    def reconnecting(self, device_id: str) -> bool: ...


class PoolSweep:
    """The pool's maintenance, mixed into ``HostPool``.

    The attributes are the pool's own (``services.HostPool.__init__`` sets
    them), and so are the four verbs below: adding a host is placement, so it
    stays in ``services``, and the sweep calls it through the same ``self``.
    Both are declared here so this half of the class is readable on its own and
    type-checked against them."""

    _session: AsyncSession
    _repo: CloudHostRepository
    _client: MicroCloudClient
    _devices: DeviceService
    _hub: _Hub

    # ``HostPool``'s own, called from ``maintain`` and ``_grow`` below.
    async def _provider_failing(self, now: datetime) -> int: ...
    async def _host_body(self) -> tuple[dict, int, str]: ...
    async def _require_room_to_grow(self, *, whole_machine: bool = False) -> None: ...
    async def _acquire(
        self,
        body: dict,
        home: dict | None,
        data_disk: int,
        *,
        image_version: str | None = None,
    ) -> CloudHost: ...

    async def maintain(self) -> None:
        """Keep the pool the size its sessions need.

        With ``cloud_pool_fixed_hosts``, the pool is that many ordinary hosts
        whatever the demand: one is added until there are that many, an idle
        one is kept rather than released after the hold, and hosts above that
        number are released once they are idle. Without it, a host that has had
        no sandbox on it for ``cloud_host_idle_hold_s`` is released — while the
        free slots left behind stay at least ``cloud_pool_min_free_slots`` —
        and a host is added when the free slots fall below that floor.

        Either way a host being rebuilt (``draining_since``) takes no new
        sandbox and is released as soon as none is on it. In fixed mode the
        oldest out-of-date host (``_outdated``) is drained, one at a time, when
        the other hosts have room for a whole host's worth — so a rebuild never
        costs the pool a slot. Also gives up on hosts the provider failed and on
        enrolled ones that stopped answering (``_lost``), closes the preparing
        line of every room whose sandbox's host is up, and adds the hosts the
        pool is short of.
        """
        now = datetime.now(UTC)
        fixed = settings.cloud_pool_fixed_hosts
        await self._repo.lock_pool()
        hosts = await self._repo.live()
        load = await self._repo.occupancy()
        for host in hosts:
            if host.device_id is None or self._hub.is_online(host.device_id):
                host.offline_since = None
            else:
                host.offline_since = host.offline_since or now
        failed = [
            host
            for host in hosts
            if _failed_unenrolled(host) or await self._silent(host, now)
        ]
        lost = self._lost([host for host in hosts if host not in failed], now)
        live = [host for host in hosts if host not in failed and host not in lost]
        free = sum(free_slots(host, load) for host in live if accepting(host))
        hold = timedelta(seconds=settings.cloud_host_idle_hold_s)
        # Ordinary hosts above the fixed size. They go by the old rule — idle
        # past the hold — until the pool is back at its size. A draining host is
        # not counted: it leaves for its own reason, and counting it here would
        # release another host beside it and leave the pool short.
        excess = (
            max(
                0,
                sum(
                    1
                    for host in live
                    if not host.whole_machine and host.draining_since is None
                )
                - fixed,
            )
            if fixed
            else 0
        )
        idle: list[CloudHost] = []
        for host in live:
            homes = load.get(host.id, 0)
            if host.whole_machine:
                # A whole VM goes the moment no home is on it: nobody else will
                # be placed on it, and it was never part of the floor.
                if not homes and host.machine_id is not None:
                    host.released_at = now
                    idle.append(host)
                continue
            if host.draining_since is not None:
                # Going anyway: it takes no new sandbox, and leaves the moment
                # its last one is gone rather than after the idle hold.
                if not homes and host.machine_id is not None:
                    host.released_at = now
                    idle.append(host)
                else:
                    host.idle_since = None
                continue
            if homes or host.machine_id is None or host.warm_claim_pending:
                host.idle_since = None
                continue
            idle_since = host.idle_since = host.idle_since or now
            if now - idle_since < hold:
                continue
            if fixed:
                if not excess:
                    # Part of the fixed pool: kept, however long it idles.
                    continue
                excess -= 1
            else:
                spare = free_slots(host, load) if accepting(host) else 0
                if free - spare < settings.cloud_pool_min_free_slots:
                    continue
                free -= spare
            host.released_at = now
            idle.append(host)
        await self._session.flush()
        if fixed:
            grow = (
                sum(1 for h in live if not h.whole_machine and h.released_at is None)
                < fixed
                and sum(_counts_toward_cap(h) for h in live)
                < settings.cloud_pool_max_hosts
                and not await self._provider_failing(now)
            )
        else:
            grow = (
                free < settings.cloud_pool_min_free_slots
                and sum(_counts_toward_cap(h) for h in live)
                < settings.cloud_pool_max_hosts
                and not await self._provider_failing(now)
            )
        await self._session.commit()
        for host in failed:
            await self._repo.lock_pool()
            await self._session.refresh(host)
            await self._fail(host)
        for host in lost:
            await self._lose(host)
        for host in idle:
            logger.info("cloud pool releasing idle host %s", host.hostname)
            await self._delete_at_provider(host)
        if fixed and live and not any(host.draining_since is not None for host in live):
            await self._drain_one(now)
        if grow:
            await self._grow()
        await self.settle_waiting()

    async def _grow(self) -> None:
        """Add the hosts the pool is short of, if it still is once the provider
        has been read: one in demand mode, up to the fixed size in fixed mode."""
        fixed = settings.cloud_pool_fixed_hosts
        try:
            body, data_disk, image_version = await self._host_body()
        except (MicroCloudError, ValidationError):
            logger.warning("cloud pool could not add a host", exc_info=True)
            return
        while True:
            try:
                await self._repo.lock_pool()
                load = await self._repo.occupancy()
                hosts = await self._repo.live()
                if fixed:
                    if sum(1 for host in hosts if not host.whole_machine) >= fixed:
                        await self._session.commit()
                        return
                else:
                    free = sum(
                        free_slots(host, load) for host in hosts if accepting(host)
                    )
                    if free >= settings.cloud_pool_min_free_slots:
                        await self._session.commit()
                        return
                await self._require_room_to_grow()
                host = await self._acquire(
                    body, None, data_disk, image_version=image_version
                )
            except (
                MicroCloudError,
                ValidationError,
                CloudKeepsFailing,
                CloudPoolFull,
            ):
                logger.warning("cloud pool could not add a host", exc_info=True)
                await self._session.rollback()
                return
            logger.info("cloud pool added host %s ahead of demand", host.hostname)
            if not fixed:
                return

    async def _drain_one(self, now: datetime) -> None:
        """Take the oldest out-of-date host out of rotation, if one is out of
        date and the others have room for a whole host's worth of sandboxes: a
        rebuild then costs the pool no capacity. One at a time, and never while
        the provider is failing.

        A host being rebuilt still counts as a host of the fixed pool, so its
        replacement is added only once it is gone (``maintain``), never beside
        it. Reads the provider with no lock held, then looks at the pool again
        under its lock."""
        try:
            version = await enrollment.current_host_image_version(self._client)
        except (MicroCloudError, ValidationError):
            # Without today's version no host can be called out of date; asking
            # again next sweep is cheaper than guessing.
            logger.warning(
                "cloud pool could not read the current image version", exc_info=True
            )
            return
        await self._repo.lock_pool()
        hosts = await self._repo.live()
        load = await self._repo.occupancy()
        if any(host.draining_since is not None for host in hosts):
            await self._session.commit()
            return
        if await self._provider_failing(now):
            await self._session.commit()
            return
        stale = [
            host
            for host in hosts
            if accepting(host)
            and host.machine_id is not None
            and host.device_id is not None
            and capacity(host) > 0
            and self._outdated(host, version, now)
        ]
        if not stale:
            await self._session.commit()
            return
        host = min(stale, key=lambda h: (h.created_at, h.id))
        others = sum(
            free_slots(other, load)
            for other in hosts
            if accepting(other) and other.id != host.id
        )
        if others < capacity(host):
            # The rest cannot take a whole host's worth: draining now would
            # leave the pool short while the host is rebuilt.
            await self._session.commit()
            return
        host.draining_since = now
        host.idle_since = None
        await self._session.commit()
        logger.info("cloud pool rebuilding stale host %s", host.hostname)

    def _outdated(self, host: CloudHost, version: str, now: datetime) -> bool:
        """Whether a host no longer matches what a host created now would be:
        its image version differs (NULL counts — a host from before the column
        existed recorded nothing), or it is older than
        ``cloud_host_rebuild_after_days`` when that is set."""
        if host.image_version != version:
            return True
        days = settings.cloud_host_rebuild_after_days
        return days > 0 and host.created_at < now - timedelta(days=days)

    async def _silent(self, host: CloudHost, now: datetime) -> bool:
        """An enrolled host whose connector has not reached the platform for
        ``CONNECT_GRACE``, with no session working on it yet: nothing there is
        anyone's, so it is replaced. Offline that long, not merely offline now:
        right after this backend restarts no connector has dialled back yet, and
        an idle host found offline then is a healthy one (a dev pool host was
        deleted that way a minute after a deploy, 2026-10-06 22:10)."""

        if (
            host.device_id is None
            or host.enrolled_at is None
            or now - host.enrolled_at < CONNECT_GRACE
            or self._hub.is_online(host.device_id)
            or host.offline_since is None
            or now - host.offline_since < CONNECT_GRACE
        ):
            return False
        working = await self._session.scalar(
            select(AgentSession.id)
            .where(AgentSession.work_lease["device_id"].as_string() == host.device_id)
            .limit(1)
        )
        return working is None

    def _lost(self, hosts: list[CloudHost], now: datetime) -> list[CloudHost]:
        """Enrolled pool hosts whose sandboxes will not run again: one the
        provider reports in error, one with no data disk recorded, where no
        sandbox starts without a disk limit (``sandbox_host.py``), and one
        whose connector has been away for ``LOST_AFTER``. A sandbox is disposable — what counts is what was pushed
        — so its session is not left waiting on such a host.

        A connector away is only believed while another host of the pool is
        online right now. When none is, what is down is more likely the
        platform's side — this backend just restarted, the link to the hosts is
        cut — and giving up on every host would throw away every sandbox that
        is about to come back. The provider's own error report needs no such
        check. Whole cloud VMs are left as they are."""
        pool = [
            host
            for host in hosts
            if host.device_id is not None and not host.whole_machine
        ]
        answering = any(
            host.device_id is not None and self._hub.is_online(host.device_id)
            for host in pool
        )
        return [
            host
            for host in pool
            if host.status == MachineStatus.error
            or host.sandbox_disk_gb is None
            or (
                answering
                and host.offline_since is not None
                and now - host.offline_since >= LOST_AFTER
            )
        ]

    async def _lose(self, host: CloudHost) -> None:
        """Give up on an enrolled host found lost (``_lost``), unless its
        connector came back meanwhile.

        Each session whose sandbox was there (its lease is on the host's
        device) is told so on its next tool call, which places it in a new
        sandbox. Their rows are locked before the pool, the order a tool call
        takes them in (``session_work._attempt``)."""

        device_id = host.device_id
        assert device_id is not None  # an enrolled host (``_lost``)
        worked = {
            row.id: row
            for row in await self._session.scalars(
                select(AgentSession)
                .where(AgentSession.work_lease["device_id"].as_string() == device_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        await self._repo.lock_pool()
        await self._session.refresh(host)
        error = host.status == MachineStatus.error
        unusable = error or host.sandbox_disk_gb is None
        if host.released_at is not None or (
            not unusable and self._hub.is_online(device_id)
        ):
            await self._session.commit()
            return
        # Only the provider's own error counts against it (``CloudKeepsFailing``).
        # A connector that went away may be the platform's network as much as
        # the provider's, and counting it would let a partial outage stop the
        # pool adding the very hosts the sessions are moved to.
        await self._fail(host, worked=worked, provider_failed=error)

    async def _fail(
        self,
        host: CloudHost,
        *,
        worked: dict | None = None,
        provider_failed: bool = True,
    ) -> None:
        """Give up on a host: release it, take its homes off it, and delete it
        at the provider.

        Called holding the pool. Each session is placed again on its next tool
        call. One whose sandbox was there (``worked``, its locked session rows
        by id) is told on that call that it is in a new sandbox (``LOST_KEY``),
        and its room hears that the sandbox was replaced and what was not
        pushed is gone. A room that was told a sandbox no session used yet is
        being prepared hears why it takes longer. The provider delete follows
        with no transaction open. ``provider_failed`` counts the host against
        the provider's record.
        """
        now = datetime.now(UTC)
        first = host.released_at is None
        host.released_at = host.released_at or now
        if provider_failed:
            host.failed_at = host.failed_at or now
        lines = []
        lost = replaced = 0
        for home in await self._repo.homes_on(host.id):
            row = (worked or {}).get(home.session_id)
            if row is not None:
                row.execution_request = {
                    **(row.execution_request or {}),
                    LOST_KEY: True,
                }
                lost += 1
                lines.append((home.topic_id, await tell_lost(self._session, home)))
            elif home.waiting_since is not None:
                replaced += 1
                lines.append(
                    (
                        home.topic_id,
                        await tell_replaced(self._session, home, host.whole_machine),
                    )
                )
        # After the lines: each is written from its home's row.
        await self._repo.delete_homes_on(host.id)
        if first:
            await keep_host_lost(self._session, host, lost=lost, replaced=replaced)
        await self._session.commit()
        for topic_id, line in lines:
            await publish_line(topic_id, line)
        logger.warning(
            "cloud pool gave up on host %s "
            "(status=%s, enroll_attempts=%s, offline_since=%s)",
            host.hostname,
            host.status,
            host.enroll_attempts,
            host.offline_since,
        )
        failures, _ = await self._repo.failures_since(now - PROVIDER_ERROR_WINDOW)
        if host.failed_at == now and failures >= MAX_PROVIDER_ERRORS:
            # Every new session is refused a sandbox until a probe succeeds,
            # and nobody watching the pool would otherwise know.
            logger.error(
                "cloud provider failed %s hosts within %s; the pool tries one "
                "host every %s until one comes up",
                failures,
                PROVIDER_ERROR_WINDOW,
                PROVIDER_PROBE_INTERVAL,
            )
        await self._delete_at_provider(host)

    async def _delete_at_provider(self, host: CloudHost) -> None:
        """Ask MicroCloud to delete a released host. One it refuses stays
        released, where ``release_undeleted`` asks again."""
        try:
            await self.destroy(host)
        except MicroCloudError:
            logger.warning("deleting host %s failed", host.hostname)
            await self._session.rollback()
            return
        await self._session.commit()

    async def release_undeleted(self) -> None:
        """Ask again for hosts released earlier whose delete did not land.

        MicroCloud accepts a delete and runs it later; one that then fails
        leaves the machine in `error`, already released, which this finds."""
        for host in await self._repo.list_released_undeleted():
            await self._delete_at_provider(host)

    async def settle_waiting(self) -> None:
        """Tell every room still waiting on a sandbox whose host is up now.

        The homes on hosts that are up are locked together and settled in one
        transaction. One another transaction holds is left for the next sweep
        (``lock_homes``). Each is read again under the lock: a tool call may
        have closed its line (``HostPool.tell_ready``), or a sweep removed the
        home, since ``waiting_homes`` read it."""
        whole_machine = {
            home.id: host.whole_machine
            for home, host in await self._repo.waiting_homes()
            if host.device_id is not None and self._hub.is_online(host.device_id)
        }
        if not whole_machine:
            return
        lines = []
        for home in await self._repo.lock_homes(list(whole_machine)):
            if home.waiting_since is None:
                continue
            home.waiting_since = None
            line = await tell_ready(self._session, home, whole_machine[home.id])
            lines.append((home.topic_id, line))
        await self._session.commit()
        for topic_id, line in lines:
            await publish_line(topic_id, line)

    async def settle_reservations(self) -> int:
        """Finish or drop reservations whose create call never came back.

        A backend that dies between reserving the row and hearing from the
        provider leaves a row with no machine id. Past the time a create can
        take, the provider either has the machine — adopted here by hostname,
        so a billed machine is not orphaned — or it does not, and the
        reservation (with the homes placed on it) is dropped.
        """
        cutoff = datetime.now(UTC) - timedelta(
            seconds=max(300.0, settings.microcloud_timeout_s * 2)
        )
        settled = 0
        for host in await self._repo.list_reservations_older_than(cutoff):
            try:
                remote = await self._client.find_machine(
                    host.customer_id, host.hostname
                )
            except MicroCloudError:
                logger.warning(
                    "settling reservation %s failed; provider unreachable",
                    host.hostname,
                )
                continue
            if remote is None:
                logger.warning(
                    "reservation %s never became a machine; dropped", host.hostname
                )
                await self._repo.delete(host)
            else:
                await self._repo.set_state(
                    host,
                    status=_as_status(remote.get("status")),
                    ip=remote.get("ip"),
                    ai_mode=str(remote.get("aiMode") or host.ai_mode),
                    ai_status=_as_ai_status(remote.get("aiStatus")),
                    machine_id=int(remote["id"]),
                )
                logger.info(
                    "reservation %s adopted machine %s", host.hostname, remote["id"]
                )
            settled += 1
        return settled

    async def refresh(self, host: CloudHost) -> CloudHost:
        """Bring one row in line with MicroCloud. Never raises for a provider
        problem: a host we can't reach is reported `unknown`, not lost."""
        if host.warm_claim_pending or host.machine_id is None:
            # Provider RUNNING says nothing about whether the claim completed,
            # and a reservation has no machine to ask about yet.
            return host
        settled_before = not _still_moving(host)
        try:
            remote = await self._client.get_machine(host.machine_id)
        except MicroCloudError:
            # An unreachable provider is not news about the machine. For one
            # still moving, `unknown` is the honest answer; for a settled one it
            # would report a healthy host as broken on a blip. `last_seen_at`
            # says how old what we keep is.
            now = datetime.now(UTC)
            if settled_before:
                return await self._repo.touch_seen(host, when=now)
            return await self._repo.set_state(
                host,
                status=MachineStatus.unknown,
                ip=None,
                ai_status=AiStatus.unknown,
                seen_at=now,
            )
        if remote is None:
            return await self._repo.set_state(
                host, status=MachineStatus.deleted, ip=None, seen_at=datetime.now(UTC)
            )
        return await self._repo.set_state(
            host,
            status=_as_status(remote.get("status")),
            ip=remote.get("ip"),
            ai_mode=str(remote.get("aiMode") or host.ai_mode),
            ai_status=_as_ai_status(remote.get("aiStatus")),
            seen_at=datetime.now(UTC),
        )

    async def refresh_due(self, limit: int = 10) -> int:
        """Poll MicroCloud for hosts still changing and for settled ones not
        re-checked within `microcloud_reconcile_interval_s`, and let go of the
        ones it no longer has."""
        hosts = await self._repo.list_due(
            limit,
            seen_before=datetime.now(UTC)
            - timedelta(seconds=settings.microcloud_reconcile_interval_s),
        )
        for host in hosts:
            try:
                if host.status not in GONE:
                    await self.refresh(host)
                if host.status in GONE:
                    await self.forget(host)
            except MicroCloudError:
                # The next tick tries again; `refresh` records the attempt.
                logger.warning("refreshing host %s failed", host.hostname)
        return len(hosts)

    async def destroy(self, host: CloudHost) -> CloudHost:
        if host.machine_id is None:
            # Never created at the provider; there is nothing to delete there.
            return await self._repo.set_state(
                host, status=MachineStatus.deleted, ip=None
            )
        await self._client.delete_machine(host.machine_id)
        return await self._repo.set_state(host, status=MachineStatus.deleting, ip=None)

    async def forget(self, host: CloudHost) -> None:
        """Drop a vanished host, its homes and the device enrolled for it."""
        if host.device_id is not None:
            device = await self._devices.get_device(host.device_id)
            if device is not None and device.supply is not Supply.cloud:
                # Structurally impossible — a host's device is enrolled by the
                # platform with supply=cloud. Refusing to delete is the safe
                # direction: the platform never destroys a machine it did not
                # open. The row still goes.
                logger.error(
                    "not deleting device %s for host %s: supply=%s, not cloud",
                    host.device_id,
                    host.hostname,
                    device.supply,
                )
            elif device is not None:
                await self._devices.delete_platform_provisioned(
                    host.device_id, actor_user_id=device.owner_user_id
                )
        if (
            host.failed_at is not None
            and datetime.now(UTC) - host.failed_at < PROVIDER_ERROR_WINDOW
        ):
            # Its row is what `_provider_failing` counts; it goes once it is out
            # of the window (`list_due` hands it back then). Its homes go now.
            await self._repo.delete_homes_on(host.id)
            return
        await self._repo.delete(host)
