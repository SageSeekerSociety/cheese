"""The platform's cloud host pool: placement, scaling and release.

A session that runs on cloud gets a home — its sandbox's directory — on any
host of the pool that has a free slot, whichever project it is from. Only when
none has room does the pool take a warm machine, and only when the warm pool is
empty does it create one, and the session waits for it. Every host is the
platform's: created under the platform's own MicroCloud customer, enrolled as a
device nobody's team can see, released once it has had no sandbox on it for
``cloud_host_idle_hold_s``.

A slot is a sandbox. A sandbox exists only while it is used: once idle it is
destroyed (``lifecycle``), and the session's next tool call places it in a new
one, on whichever host has a free slot.

A session that asks for a whole machine is given a host of its own: a whole
cloud VM created for it from ``microcloud_vm_offering_id``, which carries no
other session, is never taken from the warm pool, and is released as soon as
its home is gone. It counts against ``cloud_pool_max_hosts`` like any host.

Provisioning is asynchronous on MicroCloud's side, so nothing here blocks on it:
the pool sweep (``runner.CloudPoolSweeper``) keeps the table in line
with the provider, enrolls hosts that came up, and scales the pool.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    NotFoundError,
    ValidationError,
)
from app.domain.agent import environment
from app.domain.agent_session.models import (
    LEFT_HOST_KEY,
    LOST_KEY,
    THAW_KEY,
    AgentSession,
)
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.machine import enrollment
from app.domain.machine import holder as holders
from app.domain.machine.holder import SandboxHolder
from app.domain.machine.lifecycle import SandboxLifecycle, SandboxRemoving
from app.domain.machine.microcloud import MicroCloudClient
from app.domain.machine.models import (
    GONE,
    HOST_OWNER,
    MAX_PROVIDER_ERRORS,
    PROVIDER_ERROR_WINDOW,
    PROVIDER_PROBE_INTERVAL,
    AiStatus,
    CloudHost,
    MachineStatus,
    SandboxHome,
)
from app.domain.machine.progress import (
    tell_preparing,
    tell_ready,
)
from app.domain.machine.reaper import (
    CONNECT_GRACE,
    CloudKeepsFailing,
    CloudPoolFull,
    PoolSweep,
    _as_ai_status,
    _as_status,
    _counts_toward_cap,
    _failed_unenrolled,
    _still_moving,
    accepting,
    free_slots,
)
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.supply import data_disk_gb, effective_spec, pick_offering
from app.domain.machine.warm import WarmPoolService
from app.domain.team.services import team_service
from app.domain.usage.compute import VM, admit_start, vm_spec

logger = logging.getLogger("cheese.machine")

#: The platform's MicroCloud customer for its hosts. One for the whole pool: a
#: host carries sessions of many projects, so it is no project's cost.
CUSTOMER_REF = "cheese-platform-host-pool"

# One provider create per host per process: a session placed on a host that is
# being created waits here, holding no database lock, until the create returned.
_create_locks: dict[uuid.UUID, asyncio.Lock] = {}
# A home's ``active_at`` moves at most this often: every tool call passes
# through placement, and idleness is measured in minutes.
ACTIVE_STEP = timedelta(minutes=1)


def host_failed(host: CloudHost) -> bool:
    """Whether a host is in the pool's error state — a sandbox that could not
    come up.

    房间读那条路由借它把状态说成 ``failed`` 而不是 ``pending``（见
    ``routes/project_environment.py``）。判据收在 machine 领域里，路由就不必为了
    这一个比较 import ``machine.models`` 的 ``MachineStatus``。
    """
    return host.status == MachineStatus.error


class HostPool(PoolSweep):
    def __init__(
        self, session: AsyncSession, client: MicroCloudClient | None = None, hub=None
    ) -> None:
        # deferred-import: tests replace this name on app.domain.agent.device_hub
        from app.domain.agent.device_hub import device_hub

        self._session = session
        self._repo = CloudHostRepository(session)
        self._client = client or MicroCloudClient()
        self._devices = sql_device_service(session)
        self._hub = hub or device_hub
        self._warm_pool = WarmPoolService(session, self._client)

    @property
    def available(self) -> bool:
        return self._client.configured

    # --- placement -----------------------------------------------------------

    async def _lock_holder(self, holder_id: uuid.UUID) -> SandboxHolder:
        return await holders.lock_for_work(self._session, holder_id)

    async def place(
        self,
        session_id: uuid.UUID,
        *,
        actor: Actor,
        resource_id: str,
        whole_machine: bool = False,
    ) -> CloudHost:
        """The host this session's sandbox is on, placing it if it has none.

        ``resource_id`` names the directory the session will work in there. A
        host the provider failed before it was enrolled holds nothing of the
        session's, so the session is placed again; any other host keeps it
        until the pool sweep gives up on it (``maintain``).

        Raises ``SandboxRemoving`` while the session's idle sandbox is being
        destroyed (``lifecycle``). With ``whole_machine`` a session with no
        home is given a whole cloud VM of its own instead of a slot on a
        shared host.

        Starting a sandbox, or creating a whole VM, is charged as cloud
        compute: it raises ``ComputeRefused`` when no price is set for it or
        its holder's payer has no credits left (``holder``). A sandbox already
        running is not refused, so the turn using it finishes.
        """

        agent_session = await self._session.get(AgentSession, session_id)
        if agent_session is None:
            raise NotFoundError("agent session not found")
        # A frozen sandbox is being woken (`THAW_KEY`): it is not in use yet,
        # though its row no longer says frozen, so every look goes through the
        # pool until its executor answers again.
        thawing = bool((agent_session.execution_request or {}).get(THAW_KEY))
        holder_id = await holders.holder_of(
            self._session, agent_session.conversation_id
        )
        holder = await self._lock_holder(holder_id)
        await holder.require_use(self._session, actor)

        home = await self._repo.current_home(session_id)
        if home is not None and home.stopped_at is not None:
            raise SandboxRemoving()
        if home is not None and home.host_id is None:
            # Its sandbox was on a person's computer; that one is left to the
            # idle sweep, and the session is placed on a host below.
            await self.leave(session_id)
            home = None
        if (
            home is not None
            and home.host_id is not None
            and home.frozen_at is None
            and not thawing
        ):
            host = await self._repo.get(home.host_id)
            assert host is not None
            if host.warm_claim_pending:
                # finish_claim commits around the provider call, which lets go
                # of the locks above; take them again before deciding.
                await self._warm_pool.finish_claim(host)
                holder = await self._lock_holder(holder_id)
                await self._session.refresh(host)
                await self._session.refresh(home)
            elif host.machine_id is None and host.released_at is None:
                # Another admission is at the provider for this host. Let go of
                # the locks so it can record its answer, wait for it, then look
                # again. In another process the reservation is simply what
                # there is: the sandbox is being prepared.
                await self._session.commit()
                async with _create_locks.setdefault(host.id, asyncio.Lock()):
                    pass
                holder = await self._lock_holder(holder_id)
                await self._session.refresh(host)
                await self._session.refresh(home)
            if _still_moving(host) and host.released_at is None:
                await self.refresh(host)
            # Every tool call of the session comes through here: a sandbox that
            # is where it was is answered without the pool. One whose host
            # stopped answering is moved off it instead: the sweep gives such a
            # host up only after reaper.LOST_AFTER, and the call waiting here has
            # minutes (`leave_unusable`).
            if _keeps(host):
                if self._answers(host) or not await self.leave_unusable(
                    session_id, host.id
                ):
                    _touch(home)
                    return host
                home = None

        if home is None and not whole_machine:
            # A whole VM is admitted at its own price once its size is known
            # (``_place_on_own_vm``).
            await admit_start(self._session, await holder.payer(self._session))
        await self._repo.lock_pool()
        home = await self._repo.current_home(session_id)
        if home is not None:
            # Locked: a sweep destroying it takes the row's lock.
            home = await self._repo.lock_home(home.id)
        if home is not None and home.stopped_at is not None:
            raise SandboxRemoving()
        if home is not None and home.host_id is not None:
            host = await self._repo.get(home.host_id)
            assert host is not None
            await self._session.refresh(host)
            if host.status in GONE:
                # Gone upstream: what was there is gone with it.
                await self.forget(host)
                home = None
            elif _keeps(host):
                request = await self._session.scalar(
                    select(AgentSession.execution_request).where(
                        AgentSession.id == session_id
                    )
                )
                if home.frozen_at is not None or (request or {}).get(THAW_KEY):
                    # Frozen, or already asked to wake: hand it back now and
                    # let the install thaw it with no lock held.
                    await self._wake_frozen(home, session_id)
                    host.idle_since = None
                    await self._session.flush()
                    return host
                if self._answers(host) or not await self.leave_unusable(
                    session_id, host.id
                ):
                    _touch(home)
                    host.idle_since = None
                    await self._session.flush()
                    return host
                # Moved off a host that stopped answering; placed below.
                home = None
            else:
                await self._fail(host)
                # The locks were let go around the provider; look again.
                return await self.place(
                    session_id,
                    actor=actor,
                    resource_id=resource_id,
                    whole_machine=whole_machine,
                )
        home = {
            "spec": environment.VM if whole_machine else environment.STANDARD,
            **holder.home(),
            "resource_id": resource_id,
            "session_id": session_id,
        }
        if whole_machine:
            return await self._place_on_own_vm(session_id, holder, home)
        avoid = await self._moved_off(session_id)
        host = await self._place_on_free(home, avoid)
        if host is None and settings.cloud_sandbox_freeze:
            # No host has room: a frozen sandbox on a host that could take one
            # gives up its slot first — it is not in use, and its session
            # wakes it to a new one when it comes back. Its destroy commits,
            # so the holder and the pool are taken again before retrying.
            await self._session.commit()
            evicted = await SandboxLifecycle(
                self._session, hub=self._hub
            ).evict_one_frozen()
            await self._lock_holder(holder_id)
            await self._repo.lock_pool()
            if await self._repo.current_home(session_id) is not None:
                return await self.place(
                    session_id, actor=actor, resource_id=resource_id
                )
            if evicted:
                host = await self._place_on_free(home, avoid)
                if host is not None:
                    return host
        if host is not None:
            return host
        # No host has room. The provider is read with no lock held; another
        # admission may have added a host meanwhile, so look again after.
        await self._require_room_to_grow()
        await self._session.commit()
        body, data_disk, image_version = await self._host_body()
        await self._lock_holder(holder_id)
        await self._repo.lock_pool()
        if await self._repo.current_home(session_id) is not None:
            return await self.place(session_id, actor=actor, resource_id=resource_id)
        host = await self._place_on_free(home, avoid)
        if host is not None:
            return host
        await self._require_room_to_grow()
        return await self._acquire(body, home, data_disk, image_version=image_version)

    async def current_home(self, session_id: uuid.UUID) -> SandboxHome | None:
        return await self._repo.current_home(session_id)

    async def _place_on_own_vm(
        self, session_id: uuid.UUID, holder: SandboxHolder, home: dict
    ) -> CloudHost:
        """Create a whole cloud VM for the session, with its home on it. Called
        holding the holder and the pool; the provider is read with neither
        held."""
        await self._require_room_to_grow(whole_machine=True)
        await self._session.commit()
        body = await self._vm_body()
        await admit_start(
            self._session,
            await holder.payer(self._session),
            kind=VM,
            spec=vm_spec(body["cores"], body["memoryMb"]),
        )
        await self._lock_holder(holder.id)
        await self._repo.lock_pool()
        existing = await self._repo.current_home(session_id)
        if existing is not None and existing.host_id is not None:
            # Another tool call of the session placed it meanwhile.
            host = await self._repo.get(existing.host_id)
            assert host is not None
            await self._session.commit()
            return host
        await self._require_room_to_grow(whole_machine=True)
        return await self._create(body, home, whole_machine=True)

    async def _place_on_free(
        self, home: dict, avoid: uuid.UUID | None = None
    ) -> CloudHost | None:
        """Put ``home`` on a host with a free slot, if there is one. Called
        holding the pool; commits when it placed.

        ``avoid`` is a host this session is deliberately leaving: a room that
        asked for the sandbox it already has asks for another one, and the host
        it just left is the one with the fewest free slots, which is the one
        placement above prefers. When nothing else has room it is placed there
        anyway — that is still a sandbox, and a session with none is worse
        off."""
        load = await self._repo.occupancy()
        now = datetime.now(UTC)
        free = [
            host
            for host in await self._repo.live()
            if accepting(host)
            and free_slots(host, load)
            and self._can_start(host, now)
            and host.id != avoid
        ]
        if not free and avoid is not None:
            return await self._place_on_free(home)
        if not free:
            return None

        # A host that can run the sandbox now first; among those, the one with
        # the fewest free slots, so the others can empty and be released.
        def rank(host: CloudHost):
            ready = host.device_id is not None and self._hub.is_online(host.device_id)
            return (not ready, free_slots(host, load), host.created_at)

        host = min(free, key=rank)
        await self._repo.put_home(host.id, home)
        host.idle_since = None
        await self._placed_off(home["session_id"])
        await self._session.commit()
        return host

    async def _provider_failing(self, now: datetime) -> int:
        """How many hosts the provider failed within the window, when that is
        enough to stop asking for another right now; 0 when one may be asked
        for. Past the probe interval since the last failure one more is tried,
        so a provider that recovered is used again within minutes."""
        failures, last = await self._repo.failures_since(now - PROVIDER_ERROR_WINDOW)
        if failures < MAX_PROVIDER_ERRORS or last is None:
            return 0
        return failures if now - last < PROVIDER_PROBE_INTERVAL else 0

    def _can_start(self, host: CloudHost, now: datetime) -> bool:
        """Whether a sandbox placed on ``host`` can start soon: its connector
        is up, or the host is new enough that it may still be dialling in. One
        enrolled past ``CONNECT_GRACE`` whose connector is gone may stay gone
        for hours, and a session put there waits on it all that time."""
        if host.device_id is None or self._hub.is_online(host.device_id):
            return True
        return host.enrolled_at is None or now - host.enrolled_at < CONNECT_GRACE

    def _answers(self, host: CloudHost) -> bool:
        """Whether a call to this host can be answered: its connector is up, or
        it dropped moments ago and the call waits for it to come back
        (``device_hub.reconnecting``). The same line ``session_work._reachable``
        draws before making a call. A host with no machine yet answers nothing:
        one still dialling in is waited for, not left."""
        return host.device_id is not None and (
            self._hub.is_online(host.device_id)
            or self._hub.reconnecting(host.device_id)
        )

    async def _require_room_to_grow(self, *, whole_machine: bool = False) -> None:
        """Refuse to add a host while the provider keeps failing them, or when
        the pool is at its cap. Called holding the pool."""
        failures = await self._provider_failing(datetime.now(UTC))
        if failures:
            raise CloudKeepsFailing(failures, whole_machine=whole_machine)
        hosts = await self._repo.live()
        if sum(_counts_toward_cap(h) for h in hosts) >= settings.cloud_pool_max_hosts:
            raise CloudPoolFull(whole_machine=whole_machine)

    async def _host_body(self) -> tuple[dict, int, str]:
        """What every new host is asked for, the data disk its offering gives
        it in GB, and the image version a host made of this now carries. Reads
        the provider: call it with no lock held."""
        offering = await pick_offering(self._client)
        disk = data_disk_gb(offering)
        body = await self._body(
            offering,
            cores=settings.microcloud_default_cores,
            memory_mb=settings.microcloud_default_memory_mb,
            disk_gb=settings.microcloud_default_disk_gb,
        )
        # From the body, not the settings: a value the offering clamped is what
        # the machine actually gets, and what a later version compares against.
        version = enrollment.host_image_version(
            offering,
            cores=body["cores"],
            memory_mb=body["memoryMb"],
            disk_gb=body["diskGb"],
        )
        return body, disk, version

    async def _vm_body(self) -> dict:
        """What every whole cloud VM is asked for. Reads the provider: call it
        with no lock held."""
        return await self._body(
            await pick_offering(self._client, settings.microcloud_vm_offering_id),
            cores=settings.cloud_vm_cores,
            memory_mb=settings.cloud_vm_memory_mb,
            disk_gb=settings.cloud_vm_disk_gb,
        )

    async def _body(
        self, offering: dict, *, cores: int, memory_mb: int, disk_gb: int
    ) -> dict:
        spec = effective_spec(
            offering, cores=cores, memory_mb=memory_mb, disk_gb=disk_gb
        )
        customer_id, account_id = await self._platform_account()
        return {
            "customerId": customer_id,
            "accountId": account_id,
            "offeringId": int(offering["id"]),
            "user": settings.microcloud_login_user,
            # No built-in AI channel: the host only executes tools, and its
            # sessions' models come from the session host. Without the field
            # MicroCloud would wire its default channel onto the machine.
            "aiMode": "none",
            **spec,
        }

    async def _acquire(
        self,
        body: dict,
        home: dict | None,
        data_disk: int,
        *,
        image_version: str | None = None,
    ) -> CloudHost:
        """One more host for the pool, holding ``home`` if given: a warm machine
        when one is ready, else a new one with a data disk of ``data_disk`` GB.
        Called holding the pool; returns with no transaction open.

        ``image_version`` is what a host created this way is made of. A warm
        machine keeps none: it was prepared before any of this was recorded, so
        its row says unknown.

        A fixed pool does not take warm machines at all: it keeps its own hosts
        up, and the warm sweep deletes the unused ones (``models.warm_pool_target``).
        Adopting one would add a host whose version is unknown only to have the
        sweep roll it at once — one machine claimed and deleted for nothing.
        """
        if not settings.cloud_pool_fixed_hosts:
            warm = await self._warm_pool.reserve(body=body, home=home)
            if warm is not None:
                return warm
        return await self._create(
            body, home, data_disk=data_disk, image_version=image_version
        )

    async def _create(
        self,
        body: dict,
        home: dict | None,
        *,
        whole_machine: bool = False,
        data_disk: int | None = None,
        image_version: str | None = None,
    ) -> CloudHost:
        host_id = uuid.uuid4()
        prefix = "vm" if whole_machine else "host"
        body = {**body, "hostname": f"{prefix}-{host_id.hex[:16]}"}
        # The platform needs its own way in to enroll the host later. The
        # operator's key too: the bootstrap key is erased at enrollment, and a
        # machine nobody can log into cannot be diagnosed (see the setting).
        bootstrap_private, bootstrap_public = await enrollment.generate_keypair()
        authorized = enrollment.combine_authorized_keys(
            bootstrap_public, settings.microcloud_operator_ssh_pubkey
        )
        if authorized:
            body["sshPubkey"] = authorized
        # The row is the reservation: the pool counts it from this commit on,
        # so the pool lock (and the room's) can be let go before the provider
        # is asked. A lock held across that call kept every other admission
        # waiting with a pool connection each (dev outage of 2026-09-18). A
        # reservation whose create never came back is settled by
        # `settle_reservations`.
        host = await self._repo.add(
            id=host_id,
            machine_id=None,
            customer_id=body["customerId"],
            account_id=body["accountId"],
            offering_id=body["offeringId"],
            hostname=body["hostname"],
            login_user=body["user"],
            cores=body["cores"],
            memory_mb=body["memoryMb"],
            disk_gb=body["diskGb"],
            # The offering's word until the host enrolls and says what it has.
            sandbox_disk_gb=data_disk,
            status=MachineStatus.provisioning,
            ai_mode="none",
            ai_status=AiStatus.unknown,
            bootstrap_key=bootstrap_private,
            image_version=image_version,
            whole_machine=whole_machine,
            project_id=(
                home["project_id"] if whole_machine and home is not None else None
            ),
        )
        if home is not None:
            await self._repo.put_home(host.id, home)
        async with _create_locks.setdefault(host.id, asyncio.Lock()):
            await self._session.commit()
            logger.info("cloud pool creating host %s", host.hostname)
            try:
                created = await self._client.create_machine(body)
            except BaseException:
                # Nothing was created, so nothing is owed: give the slot back.
                await self._repo.delete(host)
                await self._session.commit()
                raise
            host = await self._repo.set_state(
                host,
                status=_as_status(created.get("status")),
                ip=created.get("ip"),
                ai_mode=str(created.get("aiMode") or "none"),
                ai_status=_as_ai_status(created.get("aiStatus")),
                machine_id=int(created["id"]),
            )
            await self._session.commit()
        return host

    async def _platform_account(self) -> tuple[int, int]:
        """The platform's MicroCloud customer + funded account for its hosts."""
        customer = await self._client.find_customer(CUSTOMER_REF)
        if customer is None:
            customer = await self._client.create_customer(CUSTOMER_REF)
        customer_id = int(customer["id"])
        name = settings.microcloud_account_name
        account = await self._client.find_account(customer_id, name)
        if account is None:
            account = await self._client.create_account(customer_id, name)
        account_id = int(account["id"])
        floor = settings.microcloud_initial_funds
        if floor > 0 and float(account.get("balance", 0)) < floor:
            await self._client.topup(account_id, floor, remark=CUSTOMER_REF)
        return customer_id, account_id

    async def leave(self, session_id: uuid.UUID) -> CloudHost | None:
        """The session moves off its machine. Its home stays where it is, no
        session's, for the idle sweep to destroy like any sandbox
        (``lifecycle``); on a host it holds its slot until then, since the
        sandbox runs there until then. A home with nothing on the machine yet
        (its host not enrolled) goes at once, and so does a whole VM's, which
        the pool releases once no home is on it.

        Answers the shared host it left that home on, which the session may be
        placed on again; None when it left nothing there. A caller that moved
        the session off on purpose asks for its next sandbox to be somewhere
        else (`_moved_off`)."""
        await self._repo.lock_pool()
        home = await self._repo.current_home(session_id)
        if home is None:
            return None
        host = None if home.host_id is None else await self._repo.get(home.host_id)
        if host is not None and (host.device_id is None or host.whole_machine):
            await self._repo.delete_home(home)
            return None
        home.session_id = None
        await self._session.flush()
        return host

    async def _moved_off(self, session_id: uuid.UUID) -> uuid.UUID | None:
        """The host this session asked to be moved off, if it has not been
        placed somewhere since: it asked for the sandbox it already had again
        (``session_work.switch_environment``), which means another one. Placing
        it back on that host — the one it just left, with the fewest free slots
        — would answer that request with the machine it was asking to get away
        from."""
        row = await self._session.get(AgentSession, session_id)
        raw = ((row.execution_request or {}) if row is not None else {}).get(
            LEFT_HOST_KEY
        )
        try:
            return uuid.UUID(raw) if raw else None
        except (TypeError, ValueError):
            return None

    async def _placed_off(self, session_id: uuid.UUID) -> None:
        """Forget the host a switch moved this session off: it has a sandbox
        again, this one. Written only when there is one to forget, since this
        runs on every placement."""
        row = await self._session.get(AgentSession, session_id)
        if row is None:
            return
        request = row.execution_request or {}
        if LEFT_HOST_KEY not in request:
            return
        row.execution_request = {
            key: value for key, value in request.items() if key != LEFT_HOST_KEY
        }

    async def leave_unusable(self, session_id: uuid.UUID, host_id: uuid.UUID) -> bool:
        """Move the session off a host that cannot run its sandbox, when
        another host of the pool can: an install there timed out or its
        connector went away (`session_work._install`), or the host stopped
        answering at all (`place`).

        The pool gives a host up only after ``reaper.LOST_AFTER`` of silence
        (``reaper._lost``), which is right for the pool and longer than the tool call
        waiting on it has: that call is answered that the machine is still
        being prepared, and so is every call after it while the session stays
        there. Its sandbox is disposable — what counts is what was pushed — so
        it is placed on a host that answers instead of waiting the sweep out.
        One that had been working there is told it is in a new sandbox
        (``LOST_KEY``), as when the sweep gives the host up (``_fail``).

        Answers whether the session was moved. False when it has no sandbox on
        ``host_id``, when that host is a whole VM's — the session's own, waited
        for where it is — or when the pool has nowhere else to put it: leaving
        would not get it a sandbox any sooner.
        """
        await self._repo.lock_pool()
        home = await self._repo.current_home(session_id)
        host = await self._repo.get(host_id)
        if (
            home is None
            or host is None
            or host.id != home.host_id
            or host.device_id is None
            or host.whole_machine
        ):
            return False
        if not await self._elsewhere(host):
            return False
        # Read without a lock, and before the pool: a tool call holds the
        # session's row and then the pool, and this runs from both sides of it.
        row = await self._session.get(AgentSession, session_id)
        lease = (row.work_lease if row is not None else None) or {}
        if row is not None and (
            lease.get("status", "ready") == "ready"
            and lease.get("device_id") == host.device_id
        ):
            row.execution_request = {**(row.execution_request or {}), LOST_KEY: True}
        await self.leave(session_id)
        return True

    async def _elsewhere(self, host: CloudHost) -> bool:
        """Whether the pool has another host that can run a sandbox now: it
        would take one (``accepting``, a slot free) and its connector answers.
        Called holding the pool. A host still dialling in does not count: the
        session would wait for it exactly as it waits for the one it is on."""
        load = await self._repo.occupancy()
        return any(
            other.id != host.id
            and accepting(other)
            and other.device_id is not None
            and self._hub.is_online(other.device_id)
            and free_slots(other, load) > 0
            for other in await self._repo.live()
        )

    async def keep_on_device(
        self,
        session_id: uuid.UUID,
        *,
        holder: SandboxHolder,
        device_id: str,
        spec: str,
        resource_id: str,
    ) -> None:
        """The session's sandbox is on ``device_id``, a person's computer: its
        home there is kept up to date, or made. Every tool call of the session
        comes through here, as it does through ``place`` on a host, with its
        ``holder`` already locked. Raises ``SandboxRemoving`` while its
        previous sandbox is being destroyed."""

        home = await self._repo.current_home(session_id)
        if home is not None:
            home = await self._repo.lock_home(home.id)
        if home is not None and home.stopped_at is not None:
            raise SandboxRemoving()
        if home is not None and home.device_id == device_id:
            _touch(home)
            return
        if home is not None:
            await self.leave(session_id)
        await self._repo.put_device_home(
            device_id,
            {
                "spec": spec,
                **holder.home(),
                "resource_id": resource_id,
                "session_id": session_id,
            },
        )

    async def holds(self, session_id: uuid.UUID, device_id: str) -> bool:
        """Whether the session's sandbox is on ``device_id`` as the last tool
        call left it, read without a lock and without moving anything: its
        home is there, nothing is destroying it, its host is kept, and a tool
        call touched it within ``ACTIVE_STEP``. Only a sandbox that is all of
        these can be handed out again without ``place``/``keep_on_device``
        (``session_work._verified``); one touched longer ago goes through them,
        so its idle clock is moved exactly as often as when every call did."""

        home = await self._repo.current_home(session_id)
        if (
            home is None
            or home.stopped_at is not None
            or home.frozen_at is not None
            or home.active_at is None
            or datetime.now(UTC) - home.active_at >= ACTIVE_STEP
        ):
            return False
        request = await self._session.scalar(
            select(AgentSession.execution_request).where(AgentSession.id == session_id)
        )
        if (request or {}).get(THAW_KEY):
            # The row says it is awake but its processes are still stopped:
            # sending a call to it now would wait on the freezer.
            return False
        if home.host_id is None:
            return home.device_id == device_id
        host = await self._repo.get(home.host_id)
        return host is not None and host.device_id == device_id and _keeps(host)

    async def _wake_frozen(self, home: SandboxHome, session_id: uuid.UUID) -> None:
        """Ask for a frozen sandbox to be woken, under the pool's lock.

        The mark goes at once so a concurrent freeze sees that somebody wants
        it back (``lifecycle._freeze`` re-checks after stopping the processes
        and thaws it itself); ``THAW_KEY`` is left for the install, which does
        the host call with no database lock held (`session_work._install`).
        The idle clock moves to now: the first real tool call lands here."""
        home.frozen_at = None
        home.active_at = datetime.now(UTC)
        row = await self._session.scalar(
            select(AgentSession).where(AgentSession.id == session_id)
        )
        if row is not None:
            row.execution_request = {
                **(row.execution_request or {}),
                THAW_KEY: home.resource_id,
            }

    async def tell_waiting(self, session_id: uuid.UUID) -> dict | None:
        """Tell the session's room, once, that its sandbox is being prepared.
        Returns what to publish once committed."""

        home = await self._locked_home(session_id)
        if home is None or home.waiting_since is not None:
            return None
        home.waiting_since = datetime.now(UTC)
        return await tell_preparing(
            self._session, home, whole_machine=await self._vm(home)
        )

    async def tell_ready(self, session_id: uuid.UUID) -> dict | None:
        """Close the room's preparing line, if it was told one."""

        home = await self._locked_home(session_id)
        if home is None or home.waiting_since is None:
            return None
        home.waiting_since = None
        return await tell_ready(self._session, home, await self._vm(home))

    async def _vm(self, home: SandboxHome) -> bool:
        host = None if home.host_id is None else await self._repo.get(home.host_id)
        return host is not None and host.whole_machine

    async def _locked_home(self, session_id: uuid.UUID) -> SandboxHome | None:
        return await self._session.scalar(
            select(SandboxHome)
            .where(SandboxHome.session_id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    # --- what rooms read -------------------------------------------------------

    async def room_hosts(
        self, topic_id: uuid.UUID, room_resource_id: str
    ) -> list[CloudHost]:
        """The hosts the room's current sessions are placed on."""
        return [
            host
            for _home, host in await self._repo.room_homes(topic_id, room_resource_id)
        ]

    async def destroyed(self, device_id: str, resource_id: str | None) -> bool:
        """Whether ``device_id`` is one of the pool's hosts and holds no home
        in ``resource_id`` any more: the sandbox there was destroyed, or the
        host given up, and nothing of it is left on the host. False for any
        other machine, and for a directory not named."""
        if resource_id is None:
            return False
        host = await self._repo.by_device(device_id)
        if host is None:
            return False
        return (
            await self._session.scalar(
                select(SandboxHome.id).where(
                    SandboxHome.host_id == host.id,
                    SandboxHome.resource_id == resource_id,
                )
            )
        ) is None

    async def forget_device_homes(self, device_id: str, resource_id: str) -> None:
        """A room's cleanup removed this directory from a host."""
        await self._repo.delete_device_homes(device_id, resource_id)

    async def forget_room_homes(
        self, topic_id: uuid.UUID, room_resource_id: str
    ) -> None:
        """A room's cleanup finished: none of that generation's homes remain."""
        await self._repo.delete_room_homes(topic_id, room_resource_id)

    # --- enrollment: making the host a device ---------------------------------

    async def enroll(self, host: CloudHost) -> CloudHost:
        """Make this host a cheese device, with nobody at a keyboard.

        The device flow exists for a human with a browser. Here the platform
        asked for the machine, so it mints the credential itself and writes it
        where `auth login` would have — then `link connect` finds the machine
        already logged in and just installs the service. The device belongs to
        the platform's pool identity and to no team or project.
        """
        if host.device_id:
            return host
        if not host.ip or not host.bootstrap_key:
            raise ValidationError("host is not ready to be enrolled")
        origin = settings.connector_public_base.rstrip("/")
        if not origin or "localhost" in origin or "127.0.0.1" in origin:
            # The machine has to reach this origin from its own network; a
            # localhost default would enroll a device that can never call home.
            raise ValidationError(
                "connector_public_base must be an origin the machine can reach"
            )
        owner = await IdentityService(self._session).ensure_agent_user(
            handle=HOST_OWNER
        )
        code = await self._devices.start(f"{host.hostname} (MicroCloud)")
        device = await self._devices.approve(
            code,
            owner_user_id=owner.id,
            team_id=(
                await team_service(self._session).ensure_personal_team(owner.id)
            ).id,
            # 入口决定待遇 (#282 决定 2): the platform asked MicroCloud for this
            # machine, so the platform may reclaim it. A CONSTANT, never derived
            # from what the machine looks like — the identical VM enrolled by a
            # human through the connector is `self_hosted` and untouchable.
            supply=Supply.cloud,
            name=host.hostname,
        )
        if settings.microcloud_direct_control:
            await self._session.execute(
                update(DeviceRow)
                .where(DeviceRow.device_id == device.device_id)
                .values(cloud_control_private=True)
            )
        token = await self._devices.bootstrap_token(device.device_id)
        # The device row and its token have to be visible to the connector route
        # before the machine dials in, and the bootstrap below makes it dial in
        # while this sweep's transaction is still open. Uncommitted, the first
        # `link connect` was answered 403 (unknown device token) on 2026-09-02.
        await self._session.commit()
        script = enrollment.bootstrap_script(
            origin=origin,
            token=token,
            device_id=device.device_id,
            sandbox_disk=not host.whole_machine,
        )
        try:
            output = await enrollment.run_bootstrap(
                ip=host.ip,
                login_user=host.login_user,
                private_key=host.bootstrap_key,
                script=script,
                sandboxes=not host.whole_machine,
            )
            disk_gb = None if host.whole_machine else enrollment.sandbox_disk_gb(output)
        except enrollment.EnrollmentError as exc:
            # Never let the token reach a log line or an API error body.
            reason = enrollment.redact(str(exc), token)
            logger.warning("enrolling host %s failed: %s", host.hostname, reason)
            # The next attempt mints a device of its own, and nothing points at
            # this one: the host records a device only once it enrolls. Kept, a
            # host that took three tries is left as three devices of one name,
            # two of them never online (2026-10-07).
            await self._devices.delete_platform_provisioned(
                device.device_id, actor_user_id=owner.id
            )
            return await self._repo.mark_enroll_failed(host, error=reason)
        logger.info(
            "enrolled host %s as device %s: %s",
            host.hostname,
            device.device_id,
            enrollment.redact(output, token)[-200:],
        )
        return await self._repo.mark_enrolled(
            host,
            device_id=device.device_id,
            when=datetime.now(UTC),
            sandbox_disk_gb=disk_gb,
        )

    async def enroll_pending(self, limit: int = 5) -> dict[str, int]:
        """Enroll every host that is up but not yet a device.

        Runs on a clock rather than in a request: it SSHes into a machine,
        which is far too slow to hang a read on, and it must keep happening for
        a host that became ready while nobody was looking.
        """
        enrolled = failed = 0
        for host in await self._repo.list_awaiting_enrollment(limit):
            try:
                result = await self.enroll(host)
            except Exception:  # one host's failure must not stop the rest
                logger.exception("enrolling host %s raised", host.hostname)
                failed += 1
                continue
            if result.device_id:
                enrolled += 1
            else:
                failed += 1
        return {"enrolled": enrolled, "failed": failed}


def _touch(home: SandboxHome) -> None:
    now = datetime.now(UTC)
    if home.active_at is None or now - home.active_at >= ACTIVE_STEP:
        home.active_at = now


def _keeps(host: CloudHost) -> bool:
    """Whether a session placed on this host stays there."""
    return (
        host.released_at is None
        and host.status not in GONE
        and not _failed_unenrolled(host)
    )
