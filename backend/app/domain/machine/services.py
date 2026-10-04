"""The platform's cloud host pool: placement, scaling and release.

A session that runs on cloud gets a home — its sandbox's directory — on any
host of the pool that has a free slot, whichever project it is from. Only when
none has room does the pool take a warm machine, and only when the warm pool is
empty does it create one, and the session waits for it. Every host is the
platform's: created under the platform's own MicroCloud customer, enrolled as a
device nobody's team can see, released once it has run no sandbox for
``cloud_host_idle_hold_s`` and its sleeping homes have been archived.

A slot is a running sandbox. A sandbox asleep (``lifecycle``) keeps its home on
the host's disk and no slot, so it does not keep anyone else off the host; the
disk bounds how many homes a host keeps (``disk_capacity``). A sleeping home
wakes on the same host when that host has a slot for it, and otherwise is
archived and restored where there is one.

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
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent.compute_configs import ComputeChoice
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.machine import enrollment
from app.domain.machine.lifecycle import SandboxBusy
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError
from app.domain.machine.models import (
    AI_TRANSITIONAL,
    GONE,
    HOST_OWNER,
    MAX_ENROLL_ATTEMPTS,
    MAX_PROVIDER_ERRORS,
    PROVIDER_ERROR_WINDOW,
    TRANSITIONAL,
    AiStatus,
    CloudHost,
    CloudHostHome,
    MachineStatus,
    capacity,
    disk_capacity,
)
from app.domain.machine.progress import publish_line, tell_replaced
from app.domain.machine.repositories import CloudHostRepository, Load
from app.domain.machine.supply import pick_offering
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import TopicStatus

logger = logging.getLogger("cheese.machine")

#: The platform's MicroCloud customer for its hosts. One for the whole pool: a
#: host carries sessions of many projects, so it is no project's cost.
CUSTOMER_REF = "cheese-platform-host-pool"
#: How long an enrolled host's connector has to reach the platform before the
#: pool gives up on it, when no session has started working there yet.
CONNECT_GRACE = timedelta(minutes=5)

# One provider create per host per process: a session placed on a host that is
# being created waits here, holding no database lock, until the create returned.
_create_locks: dict[uuid.UUID, asyncio.Lock] = {}
# A home's ``active_at`` moves at most this often: every tool call passes
# through placement, and idleness is measured in minutes.
ACTIVE_STEP = timedelta(minutes=1)


class CloudKeepsFailing(Exception):
    """The provider failed every host the pool asked for lately; the message is
    what the session is told instead of a sandbox."""

    def __init__(self, failures: int) -> None:
        minutes = int(PROVIDER_ERROR_WINDOW.total_seconds() // 60)
        super().__init__(say("cloudKeepsFailing", failures=failures, minutes=minutes))


class CloudPoolFull(Exception):
    """The pool holds ``cloud_pool_max_hosts`` and none has a free slot."""

    def __init__(self) -> None:
        super().__init__(say("cloudBusy"))


class SandboxMustMove(Exception):
    """The session's sandbox is asleep on a host that has no slot to wake it
    in: its home is archived from there and restored on a host that has."""

    def __init__(self, home_id: uuid.UUID) -> None:
        super().__init__(str(home_id))
        self.home_id = home_id


def free_slots(host: CloudHost, load: dict[uuid.UUID, Load]) -> int:
    """How many more sandboxes a host can be given: a slot to run in, and
    room on its disk for the home."""
    running, stored = load.get(host.id, Load(0, 0))
    return max(0, min(capacity(host) - running, disk_capacity(host) - stored))


def _busy(home: CloudHostHome, now: datetime) -> bool:
    return home.busy_until is not None and home.busy_until > now


def accepting(host: CloudHost) -> bool:
    """Whether a new session may be placed on this host."""
    return (
        host.released_at is None
        and host.failed_at is None
        and not host.draining
        and host.status not in {MachineStatus.error, MachineStatus.deleting, *GONE}
        and not _failed_unenrolled(host)
    )


def _counts_toward_cap(host: CloudHost) -> bool:
    return host.released_at is None and not host.draining


class HostPool:
    def __init__(
        self, session: AsyncSession, client: MicroCloudClient | None = None, hub=None
    ) -> None:
        from app.domain.agent.device_hub import device_hub
        from app.domain.machine.warm import WarmPoolService

        self._session = session
        self._repo = CloudHostRepository(session)
        self._projects = ProjectRepository(session)
        self._client = client or MicroCloudClient()
        self._devices = sql_device_service(session)
        self._hub = hub or device_hub
        self._warm_pool = WarmPoolService(session, self._client)

    @property
    def available(self) -> bool:
        return self._client.configured

    # --- who may run on cloud ------------------------------------------------

    async def admit_choice(
        self, project_id: uuid.UUID, actor: Actor, choice: ComputeChoice
    ) -> None:
        """Let a saved cloud choice stand only for someone on the project."""
        if choice.profile == "cloud":
            await self.require_use_authority(project_id, actor)

    async def require_use_authority(self, project_id: uuid.UUID, actor: Actor) -> None:
        """Being on the project authorizes running its sessions on cloud.

        The project's roster is the whole question: its owner, its team's members
        (agents seated on the team included) and its external members. What is
        still required is a signed-in identity to look up on that roster.
        """
        user_id = actor.user_id
        if actor.via == "cheese":
            from app.domain.user.services import user_by_handle

            user = await user_by_handle(self._session, actor.handle)
            user_id = user.id if user else None
        elif actor.via != "token":
            raise AuthenticationRequiredError("Login required to use cloud compute")
        if user_id is None:
            raise AuthenticationRequiredError("Login required to use cloud compute")
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        from app.domain.membership.roster import roster

        if not any(
            m.handle == actor.handle for m in await roster(self._session, project_id)
        ):
            raise ForbiddenError(say("cloudMembersOnly"))

    # --- placement -----------------------------------------------------------

    async def _lock_room(self, topic_id: uuid.UUID):
        from app.domain.topic.services import TopicService

        topic = await TopicService(self._session).lock_for_execution(topic_id)
        # The archive path takes the same lock. Re-read after waiting so a
        # session cannot be placed from the stale pre-lock `active` state.
        await self._session.refresh(topic)
        if topic.status == TopicStatus.archived:
            raise ValidationError("archived topic cannot run on cloud")
        return topic

    async def place(
        self, session_id: uuid.UUID, *, actor: Actor, resource_id: str
    ) -> CloudHost:
        """The host this session's sandbox is on, placing it if it has none.

        ``resource_id`` names the directory the session will work in there. A
        host the provider failed before it was enrolled holds nothing of the
        session's, so the session is placed again; any other host keeps it.

        A sandbox asleep wakes where its home is when that host has a slot
        (``SandboxMustMove`` when it has none); an archived home is placed like
        a new one and keeps its archive, which the caller restores from. Raises
        ``SandboxBusy`` while something else is moving the home.
        """
        from app.domain.agent_session.models import AgentSession

        agent_session = await self._session.get(AgentSession, session_id)
        if agent_session is None:
            raise NotFoundError("agent session not found")
        topic_id = agent_session.topic_id
        topic = await self._lock_room(topic_id)
        await self.require_use_authority(topic.project_id, actor)

        home = await self._repo.current_home(session_id)
        if home is not None and home.host_id is not None:
            host = await self._repo.get(home.host_id)
            assert host is not None
            if host.warm_claim_pending:
                # finish_claim commits around the provider call, which lets go
                # of the locks above; take them again before deciding.
                await self._warm_pool.finish_claim(host)
                topic = await self._lock_room(topic_id)
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
                topic = await self._lock_room(topic_id)
                await self._session.refresh(host)
                await self._session.refresh(home)
            if _still_moving(host) and host.released_at is None:
                await self.refresh(host)
            # Every tool call of the session comes through here: a sandbox that
            # is where it was, and running, is answered without the pool.
            if _keeps(host) and home.stopped_at is None:
                if _busy(home, datetime.now(UTC)):
                    raise SandboxBusy()
                _touch(home)
                return host

        await self._repo.lock_pool()
        home = await self._repo.current_home(session_id)
        if home is not None:
            # Locked: a sweep stopping or archiving it takes the row's lock.
            home = await self._repo.lock_home(home.id)
        if home is not None and home.host_id is not None:
            host = await self._repo.get(home.host_id)
            assert host is not None
            await self._session.refresh(host)
            if host.status in GONE:
                # Gone upstream: what was there is gone with it — except a home
                # it was only to be restored on, whose work is in its archive
                # and which is placed again from there.
                await self.forget(host)
                home = await self._repo.current_home(session_id)
                if home is not None:
                    home = await self._repo.lock_home(home.id)
            elif _keeps(host):
                await self._wake(home, host)
                return host
            else:
                await self._fail(host)
                # The locks were let go around the provider; look again.
                return await self.place(
                    session_id, actor=actor, resource_id=resource_id
                )
        if home is not None:
            # Archived: placed like a new home, restored once it is there.
            if _busy(home, datetime.now(UTC)):
                raise SandboxBusy()
            _touch(home)
            home.stopped_at = None
        else:
            home = {
                "project_id": topic.project_id,
                "topic_id": topic.id,
                "room_resource_id": str(topic.resource_id or topic.id),
                "resource_id": resource_id,
                "session_id": session_id,
            }
        host = await self._place_on_free(home)
        if host is not None:
            return host
        # No host has room. The provider is read with no lock held; another
        # admission may have added a host meanwhile, so look again after.
        await self._require_room_to_grow()
        await self._session.commit()
        body = await self._host_body()
        await self._lock_room(topic_id)
        await self._repo.lock_pool()
        current = await self._repo.current_home(session_id)
        if current is not None and (
            current.host_id is not None or not isinstance(home, CloudHostHome)
        ):
            return await self.place(session_id, actor=actor, resource_id=resource_id)
        if current is not None:
            home = current
        host = await self._place_on_free(home)
        if host is not None:
            return host
        await self._require_room_to_grow()
        return await self._acquire(body, home)

    async def _wake(self, home: CloudHostHome, host: CloudHost) -> None:
        """Let the session's sandbox run where its home is. Called holding the
        pool, which the count of the host's running sandboxes needs."""
        if _busy(home, datetime.now(UTC)):
            raise SandboxBusy()
        if home.stopped_at is not None:
            from app.domain.machine.lifecycle import archives_configured

            running = (await self._repo.occupancy()).get(host.id, Load(0, 0)).running
            # With no bucket to move it through, it wakes where its files are,
            # one over the host's slots, rather than not at all.
            if running >= capacity(host) and archives_configured():
                raise SandboxMustMove(home.id)
            home.stopped_at = None
        _touch(home)
        host.idle_since = None
        await self._session.flush()

    async def current_home(self, session_id: uuid.UUID) -> CloudHostHome | None:
        return await self._repo.current_home(session_id)

    async def _place_on_free(self, home: dict | CloudHostHome) -> CloudHost | None:
        """Put ``home`` on a host with a free slot, if there is one. Called
        holding the pool; commits when it placed."""
        load = await self._repo.occupancy()
        free = [
            host
            for host in await self._repo.live()
            if accepting(host) and free_slots(host, load)
        ]
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
        await self._session.commit()
        return host

    async def _require_room_to_grow(self) -> None:
        """Refuse to add a host while the provider keeps failing them, or when
        the pool is at its cap. Called holding the pool."""
        failures = await self._repo.failures_since(
            datetime.now(UTC) - PROVIDER_ERROR_WINDOW
        )
        if failures >= MAX_PROVIDER_ERRORS:
            raise CloudKeepsFailing(failures)
        hosts = await self._repo.live()
        if sum(_counts_toward_cap(h) for h in hosts) >= settings.cloud_pool_max_hosts:
            raise CloudPoolFull()

    async def _host_body(self) -> dict:
        """What every new host is asked for. Reads the provider: call it with
        no lock held."""
        offering = await pick_offering(self._client)
        spec = {
            name: max(
                int(offering[f"{name}Min"]), min(int(offering[f"{name}Max"]), value)
            )
            for name, value in (
                ("cores", settings.microcloud_default_cores),
                ("memoryMb", settings.microcloud_default_memory_mb),
                ("diskGb", settings.microcloud_default_disk_gb),
            )
        }
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
        self, body: dict, home: dict | CloudHostHome | None
    ) -> CloudHost:
        """One more host for the pool, holding ``home`` if given: a warm machine
        when one is ready, else a new one. Called holding the pool; returns
        with no transaction open."""
        warm = await self._warm_pool.reserve(body=body, home=home)
        if warm is not None:
            return warm
        return await self._create(body, home)

    async def _create(self, body: dict, home: dict | CloudHostHome | None) -> CloudHost:
        host_id = uuid.uuid4()
        body = {**body, "hostname": f"host-{host_id.hex[:16]}"}
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
            status=MachineStatus.provisioning,
            ai_mode="none",
            ai_status=AiStatus.unknown,
            bootstrap_key=bootstrap_private,
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

    async def leave(self, session_id: uuid.UUID, *, kept_work: bool) -> None:
        """The session moves off its host. Its home goes, unless the session
        did work there that it did not push (``kept_work``): then the home
        stays, on its host or in its archive, until the room's cleanup removes
        it."""
        await self._repo.lock_pool()
        home = await self._repo.current_home(session_id)
        if home is None:
            return
        if kept_work:
            home.left_at = datetime.now(UTC)
            home.waiting_since = None
            await self._session.flush()
            return
        key = home.archive_key
        await self._repo.delete_home(home)
        if key is not None:
            from app.domain.machine.lifecycle import delete_archive

            await delete_archive(key)

    async def archived(self, session_id: uuid.UUID) -> bool:
        """Whether the session's work is in its archive: archived, or placed
        on a host and not restored there yet. Whatever that host has of it is
        not the work, so nothing there is to be pushed."""
        home = await self._repo.current_home(session_id)
        return home is not None and home.archive_key is not None

    async def tell_waiting(
        self, session_id: uuid.UUID, sentence: str = "sandboxPreparing"
    ) -> dict | None:
        """Tell the session's room, once, that its sandbox is being prepared —
        or woken, or restored (``sentence``). Returns what to publish once
        committed."""
        from app.domain.machine.progress import tell_preparing

        home = await self._locked_home(session_id)
        if home is None or home.waiting_since is not None:
            return None
        home.waiting_since = datetime.now(UTC)
        return await tell_preparing(self._session, home, sentence)

    async def tell_ready(self, session_id: uuid.UUID) -> dict | None:
        """Close the room's preparing line, if it was told one."""
        from app.domain.machine.progress import tell_ready

        home = await self._locked_home(session_id)
        if home is None or home.waiting_since is None:
            return None
        home.waiting_since = None
        return await tell_ready(self._session, home)

    async def _locked_home(self, session_id: uuid.UUID) -> CloudHostHome | None:
        return await self._session.scalar(
            select(CloudHostHome)
            .where(
                CloudHostHome.session_id == session_id,
                CloudHostHome.left_at.is_(None),
            )
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

    async def forget_device_homes(self, device_id: str, resource_id: str) -> None:
        """A room's cleanup removed this directory from a host."""
        await self._repo.delete_device_homes(device_id, resource_id)

    async def forget_room_homes(
        self, topic_id: uuid.UUID, room_resource_id: str
    ) -> None:
        """A room's cleanup finished: none of that generation's homes remain,
        on a host or in the bucket."""
        from app.domain.machine.lifecycle import delete_archive

        for home in await self._repo.room_archives(topic_id, room_resource_id):
            await delete_archive(home.archive_key, missing_ok=False)
        await self._repo.delete_room_homes(topic_id, room_resource_id)

    async def archived_resources(self, topic_id: uuid.UUID) -> set[str]:
        """The room's session directories whose work is in the bucket: a
        room's cleanup has nothing to ask the machine their lease names about
        them. A copy half restored on a host is found by that host's
        inventory, like any directory there."""
        return {home.resource_id for home in await self._repo.room_archives(topic_id)}

    # --- the sweep -----------------------------------------------------------

    async def maintain(self) -> None:
        """Keep the pool the size its sessions need.

        A host that has run no sandbox for ``cloud_host_idle_hold_s`` is let
        go — while the free slots left behind stay at least
        ``cloud_pool_min_free_slots``. One with no home on it is released; one
        whose sleeping homes are still on its disk is set draining, and the
        sandbox sweep archives them (``lifecycle``), after which it is
        released here. Also gives up on hosts the provider failed, closes the
        preparing line of every room whose sandbox's host is up, and adds a
        host when the free slots fall below that floor.
        """
        from app.domain.machine.lifecycle import archives_configured

        now = datetime.now(UTC)
        await self._repo.lock_pool()
        hosts = await self._repo.live()
        load = await self._repo.occupancy()
        failed = [
            host
            for host in hosts
            if _failed_unenrolled(host) or await self._silent(host, now)
        ]
        live = [host for host in hosts if host not in failed]
        free = sum(free_slots(host, load) for host in live if accepting(host))
        hold = timedelta(seconds=settings.cloud_host_idle_hold_s)
        idle: list[CloudHost] = []
        for host in live:
            running, stored = load.get(host.id, Load(0, 0))
            if running or host.machine_id is None or host.warm_claim_pending:
                host.idle_since = None
                continue
            idle_since = host.idle_since = host.idle_since or now
            if now - idle_since < hold:
                continue
            # Draining hosts take no new session, so their slots are no buffer.
            spare = free_slots(host, load) if accepting(host) else 0
            if free - spare < settings.cloud_pool_min_free_slots:
                continue
            if stored:
                # Its homes are asleep there. Archived, they no longer keep it;
                # without a bucket to archive to, they do.
                if not host.draining and archives_configured():
                    host.draining = True
                    free -= spare
                continue
            free -= spare
            host.released_at = now
            idle.append(host)
        await self._session.flush()
        grow = (
            free < settings.cloud_pool_min_free_slots
            and sum(_counts_toward_cap(h) for h in live) < settings.cloud_pool_max_hosts
            and await self._repo.failures_since(now - PROVIDER_ERROR_WINDOW)
            < MAX_PROVIDER_ERRORS
        )
        await self._session.commit()
        for host in failed:
            await self._repo.lock_pool()
            await self._session.refresh(host)
            await self._fail(host)
        for host in idle:
            logger.info("cloud pool releasing idle host %s", host.hostname)
            await self._delete_at_provider(host)
        if grow:
            await self._grow()
        await self.settle_waiting()

    async def _grow(self) -> None:
        """Add a host ahead of demand, if the free slots are still short once
        the provider has been read."""
        try:
            body = await self._host_body()
            await self._repo.lock_pool()
            load = await self._repo.occupancy()
            hosts = await self._repo.live()
            free = sum(free_slots(host, load) for host in hosts if accepting(host))
            if free >= settings.cloud_pool_min_free_slots:
                await self._session.commit()
                return
            await self._require_room_to_grow()
            host = await self._acquire(body, None)
        except (MicroCloudError, ValidationError, CloudKeepsFailing, CloudPoolFull):
            logger.warning("cloud pool could not add a host", exc_info=True)
            await self._session.rollback()
            return
        logger.info("cloud pool added host %s ahead of demand", host.hostname)

    async def _silent(self, host: CloudHost, now: datetime) -> bool:
        """An enrolled host whose connector never reached the platform, with no
        session working on it yet: nothing there is anyone's, so it is replaced."""
        from app.domain.agent_session.models import AgentSession

        if (
            host.device_id is None
            or host.draining
            or host.enrolled_at is None
            or now - host.enrolled_at < CONNECT_GRACE
            or self._hub.is_online(host.device_id)
        ):
            return False
        working = await self._session.scalar(
            select(AgentSession.id)
            .where(AgentSession.work_lease["device_id"].as_string() == host.device_id)
            .limit(1)
        )
        return working is None

    async def _fail(self, host: CloudHost) -> None:
        """Give up on a host the provider failed before anyone worked on it.

        Called holding the pool. Its homes hold nothing — no session worked
        there — so their sessions are placed again on their next tool call, and
        each room that was told its sandbox is being prepared hears why it
        takes longer. The provider delete follows with no transaction open.
        """
        now = datetime.now(UTC)
        host.released_at = host.released_at or now
        if not host.draining:
            # A pool host counts against the provider's record. One adopted from
            # before the pool says nothing about how the provider does now.
            host.failed_at = host.failed_at or now
        lines = []
        await self._repo.unplace_archived(host.id)
        for home in await self._repo.homes_on(host.id):
            if home.waiting_since is not None:
                lines.append((home.topic_id, await tell_replaced(self._session, home)))
            await self._repo.delete_home(home)
        await self._session.commit()
        for topic_id, line in lines:
            await publish_line(topic_id, line)
        logger.warning(
            "cloud pool gave up on host %s (status=%s, enroll_attempts=%s)",
            host.hostname,
            host.status,
            host.enroll_attempts,
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
        """Tell every room still waiting on a sandbox whose host is up now."""
        for home, host in await self._repo.waiting_homes():
            if host.device_id is None or not self._hub.is_online(host.device_id):
                continue
            locked = await self._session.scalar(
                select(CloudHostHome)
                .where(CloudHostHome.id == home.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if locked is None or locked.waiting_since is None:
                await self._session.commit()
                continue
            from app.domain.machine.progress import tell_ready

            locked.waiting_since = None
            line = await tell_ready(self._session, locked)
            await self._session.commit()
            await publish_line(locked.topic_id, line)

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
        await self._repo.delete(host)

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
        # The device row and its token have to be visible to the connector route
        # before the machine dials in, and the bootstrap below makes it dial in
        # while this sweep's transaction is still open. Uncommitted, the first
        # `link connect` was answered 403 (unknown device token) on 2026-09-02.
        await self._session.commit()
        script = enrollment.bootstrap_script(
            origin=origin, token=device.token, device_id=device.device_id
        )
        try:
            output = await enrollment.run_bootstrap(
                ip=host.ip,
                login_user=host.login_user,
                private_key=host.bootstrap_key,
                script=script,
            )
        except enrollment.EnrollmentError as exc:
            # Never let the token reach a log line or an API error body.
            reason = enrollment.redact(str(exc), device.token)
            logger.warning("enrolling host %s failed: %s", host.hostname, reason)
            return await self._repo.mark_enroll_failed(host, error=reason)
        logger.info(
            "enrolled host %s as device %s: %s",
            host.hostname,
            device.device_id,
            enrollment.redact(output, device.token)[-200:],
        )
        return await self._repo.mark_enrolled(
            host, device_id=device.device_id, when=datetime.now(UTC)
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


def _touch(home: CloudHostHome) -> None:
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
