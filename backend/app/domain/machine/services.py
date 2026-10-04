"""Project machine business logic.

Gives a project real compute: one call provisions a Debian machine from
MicroCloud, bills it to a fund account owned by that project, and hands back an
address someone can SSH into. Provisioning is asynchronous on MicroCloud's side,
so this service never blocks on it — status is refreshed whenever a machine is
read, which also means a machine that finished (or failed) while nobody was
looking is correct the next time anyone asks.
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent.compute_configs import ComputeChoice, room_choice
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.machine import enrollment
from app.domain.machine.limits import get_machine_limit
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError
from app.domain.machine.models import (
    AI_TRANSITIONAL,
    GONE,
    MAX_ENROLL_ATTEMPTS,
    MAX_PROVIDER_ERRORS,
    PROVIDER_ERROR_WINDOW,
    TRANSITIONAL,
    AiStatus,
    MachineStatus,
    ProjectMachine,
)
from app.domain.machine.progress import (
    SETTLE_WINDOW,
    provider_errors,
    publish_line,
    startup_progress,
    tell_machine_replaced,
)
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.supply import SupplyRange, check_choice, pick_offering
from app.domain.project.repositories import ProjectRepository
from app.domain.team.services import team_service
from app.domain.topic.models import TopicStatus

# MicroCloud bills a customer, and a customer is keyed by the caller's own id.
# Scoping it to the PROJECT (not the person) matches how compute is granted in
# cheese — a project's machines are the project's cost, whoever clicked.
CUSTOMER_REF_PREFIX = "cheese-project:"

_HOSTNAME_SAFE = re.compile(r"[^a-z0-9-]+")

logger = logging.getLogger("cheese.machine")


def customer_ref(project_id: uuid.UUID) -> str:
    return f"{CUSTOMER_REF_PREFIX}{project_id}"


def derive_hostname(project_name: str, project_id: uuid.UUID, index: int) -> str:
    """A stable, DNS-safe hostname a human can recognise in a machine list."""
    slug = _HOSTNAME_SAFE.sub("-", project_name.lower()).strip("-")
    slug = slug[:20].strip("-") or "project"
    return f"{slug}-{str(project_id)[:6]}-{index}"


# One provider create per room per process: a second admission of the room
# waits here, holding no database lock, until the first has recorded the
# machine. One lock per room ever provisioned, so this stays small.
_create_locks: dict[uuid.UUID, asyncio.Lock] = {}


class CloudKeepsFailing(Exception):
    """The provider failed every machine the room asked for lately; the
    message is what the session is told instead of a machine."""

    def __init__(self, failures: int) -> None:
        minutes = int(PROVIDER_ERROR_WINDOW.total_seconds() // 60)
        super().__init__(say("cloudKeepsFailing", failures=failures, minutes=minutes))


@dataclass(frozen=True, slots=True)
class FailedLease:
    topic_id: uuid.UUID
    hostname: str
    reason: str


class MachineService:
    def __init__(
        self, session: AsyncSession, client: MicroCloudClient | None = None
    ) -> None:
        self._session = session
        self._repo = ProjectMachineRepository(session)
        self._projects = ProjectRepository(session)
        self._client = client or MicroCloudClient()
        self._devices = sql_device_service(session)
        from app.domain.machine.warm import WarmPoolService

        self._warm_pool = WarmPoolService(session, self._client)

    @property
    def available(self) -> bool:
        return self._client.configured

    async def require_manage_authority(
        self, project_id: uuid.UUID, actor: Actor, *, action: str
    ) -> None:
        """The one rule for changing a project's billed machines.

        `action` is what the person was trying to do, in their words, so a
        refusal names that operation rather than one they never attempted.
        """
        if not actor.authenticated or actor.user_id is None:
            raise AuthenticationRequiredError(say("machineSignInTo", action=action))
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        teams = team_service(self._session)
        if not await teams.is_team_member(project.team_id, actor.user_id):
            raise NotFoundError("Project not found")
        if not await teams.is_team_at_least_admin(project.team_id, actor.user_id):
            raise ForbiddenError(say("machineAdminOnlyTo", action=action))

    async def admit_choice(
        self, project_id: uuid.UUID, actor: Actor, choice: ComputeChoice
    ) -> None:
        """Let a saved choice stand only if it can be honoured and paid for.

        A self-hosted choice spends nobody's cloud quota and has no supply range.
        A cloud one must fit what the provider offers now (when that can be
        read) and be chosen by someone the project lets spend its quota.
        """
        if choice.profile != "cloud":
            return
        await check_choice(choice)
        await self.require_use_authority(project_id, actor)

    async def require_use_authority(self, project_id: uuid.UUID, actor: Actor) -> None:
        """Being on the project authorizes room execution within the team's quota.

        The project's roster is the whole question: its owner, its team's members
        (agents seated on the team included) and its external members. Who spent
        how much is not decided per person here. What is still required is a
        signed-in identity to look up on that roster.
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
            raise ForbiddenError(say("cloudQuotaMembersOnly"))

    async def _ensure_account(self, project_id: uuid.UUID) -> tuple[int, int]:
        """The project's MicroCloud customer + funded compute account."""
        ref = customer_ref(project_id)
        customer = await self._client.find_customer(ref)
        if customer is None:
            customer = await self._client.create_customer(ref)
        customer_id = int(customer["id"])

        name = settings.microcloud_account_name
        account = await self._client.find_account(customer_id, name)
        if account is None:
            account = await self._client.create_account(customer_id, name)
        account_id = int(account["id"])

        floor = settings.microcloud_initial_funds
        if floor > 0 and float(account.get("balance", 0)) < floor:
            await self._client.topup(account_id, floor, remark=f"cheese {ref}")
        return customer_id, account_id

    async def provision(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        requested_by: str | None,
        owner_user_id: int | None = None,
        cores: int | None = None,
        memory_mb: int | None = None,
        disk_gb: int | None = None,
    ) -> ProjectMachine:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("project not found")

        team_id = await self.quota_team_id(project_id)
        # The provider reads and the account setup happen before the quota lock:
        # only counting the team's machines and creating one need to be atomic,
        # and every provider call made under the lock keeps every other
        # admission of the team waiting with a pool connection each.
        offering = await pick_offering(self._client)
        # A spec is only meaningful against the offering we actually landed on,
        # and one it cannot honour is refused, never quietly shrunk to fit.
        SupplyRange.of(offering).require(
            {"cores": cores, "memory_mb": memory_mb, "disk_gb": disk_gb}
        )

        def fill(value: int | None, default: int, lo: str, hi: str) -> int:
            # Only the standard choice arrives without numbers; its defaults
            # are the deployment's, fitted to whatever this offering allows.
            if value is not None:
                return value
            return max(int(offering[lo]), min(int(offering[hi]), default))

        spec = {
            "cores": fill(
                cores, settings.microcloud_default_cores, "coresMin", "coresMax"
            ),
            "memoryMb": fill(
                memory_mb,
                settings.microcloud_default_memory_mb,
                "memoryMbMin",
                "memoryMbMax",
            ),
            "diskGb": fill(
                disk_gb, settings.microcloud_default_disk_gb, "diskGbMin", "diskGbMax"
            ),
        }

        customer_id, account_id = await self._ensure_account(project_id)
        user = settings.microcloud_login_user

        await self._repo.lock_team_quota(team_id)
        existing = await self.quota_machines(team_id)
        limit = await get_machine_limit(self._session, team_id)
        if len(existing) >= limit:
            raise ValidationError(
                say("teamCloudMachineLimit", used=len(existing), limit=limit)
            )
        project_used = sum(m.project_id == project_id for m in existing)
        hostname = derive_hostname(project.name, project_id, project_used + 1)

        body = {
            "customerId": customer_id,
            "accountId": account_id,
            "hostname": hostname,
            "offeringId": int(offering["id"]),
            "user": user,
            # No built-in AI channel: the machine only executes tools, and its
            # sessions' models come from the session host. Without the field
            # MicroCloud would wire its default channel onto the machine.
            "aiMode": "none",
            **spec,
        }
        if owner_user_id is not None:
            warm = await self._warm_pool.reserve(
                body=body,
                project_id=project_id,
                topic_id=topic_id,
                session_id=session_id,
                requested_by=requested_by,
                owner_user_id=owner_user_id,
            )
            if warm is not None:
                await startup_progress(
                    topic_id, say("cloudWarmPicked"), machine_id=warm.id
                )
                return warm
        # The platform needs its own way in to enroll the machine later. The
        # operator's key too: the bootstrap key is erased at enrollment, and a
        # machine nobody can log into cannot be diagnosed (see the setting).
        bootstrap_private, bootstrap_public = await enrollment.generate_keypair()
        authorized = enrollment.combine_authorized_keys(
            bootstrap_public, settings.microcloud_operator_ssh_pubkey
        )
        if authorized:
            body["sshPubkey"] = authorized

        # The row is the reservation: it counts against the team's quota from
        # this commit on, so the quota lock (and whatever locks the caller holds)
        # can be let go before the provider is asked. A lock held across that
        # call kept every other admission of the team waiting with a pool
        # connection each (dev outage of 2026-09-18). A reservation whose create
        # never came back is settled by `settle_reservations`.
        machine = await self._repo.add(
            project_id=project_id,
            topic_id=topic_id,
            session_id=session_id,
            machine_id=None,
            customer_id=customer_id,
            account_id=account_id,
            offering_id=int(offering["id"]),
            hostname=hostname,
            login_user=user,
            cores=spec["cores"],
            memory_mb=spec["memoryMb"],
            disk_gb=spec["diskGb"],
            status=MachineStatus.provisioning,
            ip=None,
            requested_by=requested_by,
            ai_mode="none",
            ai_status=AiStatus.unknown,
            owner_user_id=owner_user_id,
            bootstrap_key=bootstrap_private,
        )
        async with _create_locks.setdefault(session_id or topic_id, asyncio.Lock()):
            await self._session.commit()
            await startup_progress(
                topic_id, say("cloudCreateRequested"), machine_id=machine.id
            )
            try:
                created = await self._client.create_machine(body)
            except BaseException:
                # Nothing was created, so nothing is owed: give the slot back.
                await startup_progress(
                    topic_id,
                    say("cloudCreateUnconfirmed"),
                    machine_id=machine.id,
                    failed=True,
                )
                await self._repo.delete(machine)
                await self._session.commit()
                raise
            machine = await self._repo.set_state(
                machine,
                status=_as_status(created.get("status")),
                ip=created.get("ip"),
                ai_mode=str(created.get("aiMode") or "none"),
                ai_status=_as_ai_status(created.get("aiStatus")),
                machine_id=int(created["id"]),
            )
            await self._session.commit()
            await startup_progress(
                topic_id, say("cloudCreateAccepted"), machine_id=machine.id
            )
        return machine

    async def settle_reservations(self) -> int:
        """Finish or drop reservations whose create call never came back.

        A backend that dies between reserving the row and hearing from the
        provider leaves a row with no machine id. Past the time a create can
        take, the provider either has the machine — adopted here by hostname,
        so a billed machine is not orphaned — or it does not, and the slot is
        given back.
        """
        cutoff = datetime.now(UTC) - timedelta(
            seconds=max(300.0, settings.microcloud_timeout_s * 2)
        )
        settled = 0
        for machine in await self._repo.list_reservations_older_than(cutoff):
            try:
                remote = await self._client.find_machine(
                    machine.customer_id, machine.hostname
                )
            except MicroCloudError:
                logger.warning(
                    "settling reservation %s failed; provider unreachable",
                    machine.hostname,
                )
                continue
            if remote is None:
                logger.warning(
                    "reservation %s never became a machine; slot released",
                    machine.hostname,
                )
                await self._repo.delete(machine)
            else:
                await self._repo.set_state(
                    machine,
                    status=_as_status(remote.get("status")),
                    ip=remote.get("ip"),
                    ai_mode=str(remote.get("aiMode") or machine.ai_mode),
                    ai_status=_as_ai_status(remote.get("aiStatus")),
                    machine_id=int(remote["id"]),
                )
                logger.info(
                    "reservation %s adopted machine %s", machine.hostname, remote["id"]
                )
            settled += 1
        return settled

    async def ensure_topic_machine(
        self, topic_id: uuid.UUID, *, actor: Actor | None = None
    ) -> ProjectMachine:
        """Return/create one locked lease; admission also locks the team's quota."""
        from app.domain.topic.services import TopicService

        topic = await TopicService(self._session).lock_for_execution(topic_id)
        await self._repo.lock_topic(topic_id)
        # The archive path takes the same topic lock. Re-read after waiting so a
        # first turn cannot provision from the stale pre-lock `active` state.
        await self._session.refresh(topic)
        if topic.status == TopicStatus.archived:
            raise ValidationError("archived topic cannot provision cloud compute")

        existing = await self._repo.get_active_for_topic(topic_id)
        if existing is not None:
            if existing.warm_claim_pending:
                await self._warm_pool.finish_claim(existing)
                # finish_claim commits around the provider call, which lets go
                # of the locks above; take them again before deciding.
                topic = await TopicService(self._session).lock_for_execution(topic_id)
                await self._repo.lock_topic(topic_id)
                await self._session.refresh(existing)
            elif existing.machine_id is None:
                # Another admission of this room is at the provider. Let go of
                # the locks so it can record its answer, wait for it, then look
                # again under the locks. In another process the reservation is
                # simply what there is: the room is being prepared.
                await self._session.commit()
                async with _create_locks.setdefault(topic_id, asyncio.Lock()):
                    pass
                topic = await TopicService(self._session).lock_for_execution(topic_id)
                await self._repo.lock_topic(topic_id)
                await self._session.refresh(existing)
            if _still_moving(existing) or _stale(existing):
                await self.refresh(existing)
            if existing.status == MachineStatus.suspended:
                await self.resume(existing)
            if existing.status not in GONE:
                return existing
            await self.forget(existing)

        if actor is None:
            raise AuthenticationRequiredError(
                "Cloud provisioning requires an authorized human caller"
            )
        await self.require_use_authority(topic.project_id, actor)

        project = await self._projects.get(topic.project_id)
        if project is None:
            raise NotFoundError("project not found")
        choice = room_choice(topic, project.settings)
        if choice.profile != "cloud":
            raise ValidationError(say("machineNoCloudConfig"))
        topic.compute_config = choice.model_dump()
        agent = await IdentityService(self._session).ensure_room_agent_user(topic_id)
        return await self.provision(
            project_id=topic.project_id,
            topic_id=topic_id,
            requested_by=actor.handle,
            owner_user_id=agent.id,
            cores=choice.cores,
            memory_mb=choice.memory_mb,
            disk_gb=choice.disk_gb,
        )

    async def ensure_session_machine(
        self, session_id: uuid.UUID, *, actor: Actor, choice: ComputeChoice
    ) -> ProjectMachine:
        """The room's Cloud machine for this session, rented if the room has none.

        一个话题一个容器（2026-09-28 决定，推翻结论 60）: a later agent in the
        room works on the machine the first one rented, in its own directory,
        never on a VM of its own. A session that already rented one keeps it.
        """
        from app.domain.agent_instance.models import AgentInstance
        from app.domain.agent_session.models import AgentSession
        from app.domain.topic.services import TopicService

        agent_session = await self._session.get(AgentSession, session_id)
        if agent_session is None:
            raise NotFoundError("agent session not found")
        topic_id = agent_session.topic_id
        topic = await TopicService(self._session).lock_for_execution(topic_id)
        # Shared with archive and legacy allocation; the reservation commits
        # before external I/O, so another session can start its own allocation.
        await self._repo.lock_topic(topic_id)
        await self._session.refresh(topic)
        if topic.status == TopicStatus.archived:
            raise ValidationError("archived topic cannot provision cloud compute")
        await self.require_use_authority(topic.project_id, actor)
        if choice.profile != "cloud":
            raise ValidationError("session has not selected cloud compute")

        existing = await self._repo.get_active_for_session(
            session_id
        ) or await self._repo.get_room_session_machine(topic_id)
        if existing is not None:
            if existing.warm_claim_pending:
                await self._warm_pool.finish_claim(existing)
            elif existing.machine_id is None:
                await self._session.commit()
                async with _create_locks.setdefault(
                    existing.session_id or session_id, asyncio.Lock()
                ):
                    pass
            topic = await TopicService(self._session).lock_for_execution(topic_id)
            await self._repo.lock_topic(topic_id)
            await self._session.refresh(topic)
            await self._session.refresh(existing)
            if topic.status == TopicStatus.archived:
                raise ValidationError("archived topic cannot provision cloud compute")
            if existing.released_at is not None or existing.superseded_at is not None:
                raise ConflictError("session cloud allocation was released")
            if _still_moving(existing) or _stale(existing):
                await self.refresh(existing)
            if existing.status == MachineStatus.suspended:
                await self.resume(existing)
            if existing.status == MachineStatus.error and existing.device_id is None:
                await self._replace_failed(existing)
                # The locks were let go around the provider; look again.
                return await self.ensure_session_machine(
                    session_id, actor=actor, choice=choice
                )
            if existing.status not in GONE:
                return existing
            await self.forget(existing)

        failures = await provider_errors(self._session, topic_id)
        if failures >= MAX_PROVIDER_ERRORS:
            raise CloudKeepsFailing(failures)

        instance = await self._session.scalar(
            select(AgentInstance).where(
                AgentInstance.project_id == topic.project_id,
                AgentInstance.handle == agent_session.agent_handle,
            )
        )
        if instance is None or not instance.is_active:
            raise ValidationError("session agent is not active in this project")
        agent = await IdentityService(self._session).ensure_instance_agent_user(
            instance.id, instance.display_name
        )
        return await self.provision(
            project_id=topic.project_id,
            topic_id=topic_id,
            session_id=session_id,
            requested_by=actor.handle,
            owner_user_id=agent.id,
            cores=choice.cores,
            memory_mb=choice.memory_mb,
            disk_gb=choice.disk_gb,
        )

    async def _replace_failed(self, machine: ProjectMachine) -> None:
        """Let go of a room's machine the provider failed to create.

        It was never enrolled, so none of the room's work is on it. Detaching
        it is what lets the room ask for another; the provider delete follows
        with no transaction open, and one it refuses leaves the machine to
        ``release_left_machines``.
        """
        assert machine.topic_id is not None
        failures = await provider_errors(self._session, machine.topic_id) + 1
        machine.superseded_at = datetime.now(UTC)
        line = await tell_machine_replaced(self._session, machine, failures=failures)
        await self._session.commit()
        await publish_line(machine.topic_id, line)
        await self.release_left_machine(machine.id)

    async def supersede_session_machine(
        self, session_id: uuid.UUID, *, actor: Actor
    ) -> ProjectMachine | None:
        """Detach the room's VM once the last session on it leaves; it keeps its
        files and its quota until ``release_left_machine`` or the room's cleanup
        deletes it.

        The VM is the room's, so a session leaving it while another session
        still holds a lease there leaves it standing (``None``): that session's
        work is only there until it has pushed too.

        Pending allocation stays attached until its provider outcome is known.
        """
        from app.domain.agent_session.models import AgentSession
        from app.domain.topic.services import TopicService

        agent_session = await self._session.get(AgentSession, session_id)
        if agent_session is None:
            raise NotFoundError("agent session not found")
        topic = await TopicService(self._session).lock_for_execution(
            agent_session.topic_id
        )
        await self._repo.lock_topic(topic.id)
        lease = agent_session.work_lease or {}
        machine = await self._repo.get_active_for_session(session_id)
        if machine is None and lease.get("device_id"):
            machine = await self._repo.get_room_session_machine(
                topic.id, device_id=lease["device_id"]
            )
        if machine is None:
            return None
        if machine.device_id is not None:
            others = await self._session.scalars(
                select(AgentSession.work_lease).where(
                    AgentSession.topic_id == topic.id,
                    AgentSession.id != session_id,
                    AgentSession.work_lease.is_not(None),
                )
            )
            if any(
                (other or {}).get("device_id") == machine.device_id for other in others
            ):
                return None
        await self.require_use_authority(topic.project_id, actor)
        if machine.warm_claim_pending or machine.machine_id is None:
            raise ConflictError("cloud allocation is still pending")
        machine.superseded_at = datetime.now(UTC)
        await self._session.flush()
        return machine

    async def release_left_machine(self, machine_id: uuid.UUID) -> None:
        """Delete a VM its session left once nothing of the session's work is
        only there, and stop counting it against the team's quota.

        The provider delete runs with no transaction open. A provider that
        refuses leaves the VM superseded, where the room's cleanup finds it.
        """
        machine = await self._repo.get(machine_id)
        if machine is None or machine.released_at is not None:
            return
        await self._session.commit()
        try:
            machine = await self.destroy(machine)
        except MicroCloudError:
            logger.warning("deleting left machine %s failed", machine.hostname)
            return
        await self._repo.mark_released(machine, when=datetime.now(UTC))
        await self._session.commit()

    async def release_left_machines(self) -> int:
        """Delete VMs their session left whose one delete attempt did not land.

        ``release_left_machine`` runs once, as the session leaves; a provider
        refusal or a restart before it runs leaves the VM superseded, and the
        room's cleanup only comes when the room is archived, which an active room
        never is. A VM still holding a session's unpushed work is left to that
        cleanup.
        """
        # Past the moment the leaving session itself deletes it.
        cutoff = datetime.now(UTC) - timedelta(minutes=5)
        released = 0
        for machine in await self._repo.list_left_older_than(cutoff):
            if machine.status in GONE or await left_unpushed_on(
                self._session, machine.topic_id, machine.device_id
            ):
                continue
            await self.release_left_machine(machine.id)
            await self._session.refresh(machine)
            if machine.released_at is not None:
                released += 1
        # MicroCloud accepts a delete and runs it later; one that then fails
        # leaves the VM in `error`, already released, so nothing above sees it.
        for machine in await self._repo.list_left_undeleted():
            await self._session.commit()
            try:
                await self.destroy(machine)
            except MicroCloudError:
                logger.warning("deleting left machine %s failed", machine.hostname)
                continue
            await self._session.commit()
        return released

    async def list_active_for_topic(self, topic_id: uuid.UUID) -> list[ProjectMachine]:
        return await self._repo.list_active_for_topic(topic_id)

    async def topic_machine(self, topic_id: uuid.UUID) -> ProjectMachine | None:
        return await self._repo.get_active_for_topic(topic_id)

    async def detach_archived_machine(self, topic_id: uuid.UUID) -> None:
        """Reopening gets new compute; the recorded cleanup still owns the old VM."""
        await self._repo.lock_topic(topic_id)
        machines = await self._repo.list_active_for_topic(topic_id)
        binding = await self._devices.topic_binding(topic_id)
        if binding is not None and any(
            binding.device_id == machine.device_id for machine in machines
        ):
            await self._devices.release_topic_device(
                topic_id, reason="reopen after cleanup claim"
            )
        for machine in machines:
            await self._repo.mark_released(machine, when=datetime.now(UTC))

    async def release_archived_machine(self, machine_id: uuid.UUID) -> None:
        """Delete the recorded VM even if its room now has a newer allocation."""
        machine = await self._repo.get(machine_id)
        if machine is None:
            return
        if machine.topic_id is None:
            raise ValidationError("Cleanup resource is not a room allocation")
        topic_id = machine.topic_id
        await self._repo.lock_topic(topic_id)
        if machine.device_id is not None:
            pins = await self._devices.list_topic_bindings(machine.device_id)
            if any(pin.topic_id != topic_id for pin in pins):
                raise ConflictError("Cloud machine is shared by another room")
        deleting = machine.status not in {MachineStatus.deleting, MachineStatus.deleted}
        device_id, provider_id = machine.device_id, machine.machine_id
        # The device listing and the provider delete run with no transaction
        # open; the room lock is taken again to record the outcome.
        await self._session.commit()
        if deleting:
            if device_id is not None:
                from app.domain.agent.device_provider import list_device_storage

                if await list_device_storage(device_id):
                    raise ConflictError("Cloud machine still contains room directories")
            if provider_id is not None:
                await self._client.delete_machine(provider_id)
        await self._repo.lock_topic(topic_id)
        await self._session.refresh(machine)
        if deleting:
            await self._repo.set_state(
                machine,
                status=MachineStatus.deleting
                if provider_id is not None
                else MachineStatus.deleted,
                ip=None,
            )
        binding = await self._devices.topic_binding(topic_id)
        if binding is not None and binding.device_id == machine.device_id:
            await self._devices.release_topic_device(
                topic_id, reason="archived room cleanup completed"
            )
        await self._repo.mark_released(machine, when=datetime.now(UTC))

    async def ready_topic_devices(
        self, device_id: str | None = None
    ) -> list[tuple[uuid.UUID, str]]:
        return await self._repo.list_ready_topic_devices(device_id)

    async def unsettled_startups(
        self, now: datetime | None = None
    ) -> list[ProjectMachine]:
        """Session allocations whose room may still be waiting to hear how
        their startup ended (``progress.settle_startups``)."""
        return await self._repo.list_session_startups(
            (now or datetime.now(UTC)) - SETTLE_WINDOW
        )

    async def failed_topic_leases(self) -> list[FailedLease]:
        """Topic leases that will never become ready, with the reason in words."""
        out: list[FailedLease] = []
        for machine in await self._repo.list_failed_topic_leases():
            assert machine.topic_id is not None
            reason = (
                "MicroCloud 报告机器创建失败"
                if machine.status == MachineStatus.error
                else "MicroCloud 报告机器的 AI 通道配置失败"
            )
            out.append(
                FailedLease(
                    topic_id=machine.topic_id,
                    hostname=machine.hostname,
                    reason=(
                        f"{reason}（status={machine.status}, "
                        f"ai_status={machine.ai_status}）"
                    ),
                )
            )
        return out

    async def refresh(self, machine: ProjectMachine) -> ProjectMachine:
        """Bring one row in line with MicroCloud. Never raises for a provider
        problem: a machine we can't reach is reported `unknown`, not lost."""
        if machine.warm_claim_pending or machine.machine_id is None:
            # Provider RUNNING says nothing about whether room assignment
            # completed, and a reservation has no machine to ask about yet.
            return machine
        settled_before = not _still_moving(machine)
        try:
            remote = await self._client.get_machine(machine.machine_id)
        except MicroCloudError:
            # An unreachable provider is not news about the machine. For one
            # still moving, `unknown` is the honest answer — we were waiting on
            # a state that may since have changed. For a settled one it is a
            # downgrade on a blip: it would report a healthy machine as broken.
            # Keep what we last knew; `last_seen_at` says how old that is, and
            # recording the attempt is also what stops a provider outage from
            # sending every sweep back to the same machines.
            now = datetime.now(UTC)
            if settled_before:
                return await self._repo.touch_seen(machine, when=now)
            return await self._repo.set_state(
                machine,
                status=MachineStatus.unknown,
                ip=None,
                ai_status=AiStatus.unknown,
                seen_at=now,
            )
        if remote is None:
            return await self._repo.set_state(
                machine,
                status=MachineStatus.deleted,
                ip=None,
                seen_at=datetime.now(UTC),
            )
        if machine.topic_id is not None and machine.device_id is None:
            status = _as_status(remote.get("status"))
            if status != machine.status:
                text = {
                    MachineStatus.starting: say("cloudMachineStarting"),
                    MachineStatus.running: say("cloudMachineRunning"),
                    MachineStatus.error: say("cloudMachineError"),
                }.get(status)
                if text:
                    await startup_progress(
                        machine.topic_id,
                        text,
                        machine_id=machine.id,
                        failed=status == MachineStatus.error,
                    )
        return await self._repo.set_state(
            machine,
            status=_as_status(remote.get("status")),
            ip=remote.get("ip"),
            ai_mode=str(remote.get("aiMode") or machine.ai_mode),
            ai_status=_as_ai_status(remote.get("aiStatus")),
            seen_at=datetime.now(UTC),
        )

    async def quota_team_id(self, project_id: uuid.UUID) -> int:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("project not found")
        return project.team_id

    async def quota_machines(self, team_id: int) -> list[ProjectMachine]:
        """Inventory counted by both admission and the allocation notice."""
        return [
            m
            for m in await self._repo.list_for_team(team_id)
            if m.status not in GONE and m.released_at is None
        ]

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMachine]:
        """What this table last learned; the sweep keeps it current.

        A read never asks MicroCloud. Pages poll this every few seconds, and a
        provider round-trip per machine on the request path let one open page
        on a 48-machine project hold the backend's CPU (2026-10-01).
        """
        machines = await self._repo.list_for_project(project_id)
        alive: list[ProjectMachine] = []
        for machine in machines:
            if machine.status in GONE:
                # MicroCloud has forgotten it, so there is nothing left to
                # report or to bill. Forgetting also removes the connector
                # device created for this machine, including its team binding;
                # otherwise the team pool would retain a dead "ghost" node.
                await self.forget(machine)
                continue
            alive.append(machine)
        return alive

    async def get_or_404(self, machine_row_id: uuid.UUID) -> ProjectMachine:
        machine = await self._repo.get(machine_row_id)
        if machine is None:
            raise NotFoundError("machine not found")
        return machine

    async def destroy(self, machine: ProjectMachine) -> ProjectMachine:
        if machine.machine_id is None:
            # Never created at the provider; there is nothing to delete there.
            return await self._repo.set_state(
                machine, status=MachineStatus.deleted, ip=None
            )
        await self._client.delete_machine(machine.machine_id)
        return await self._repo.set_state(
            machine, status=MachineStatus.deleting, ip=None
        )

    async def suspend(self, machine: ProjectMachine) -> ProjectMachine:
        if machine.machine_id is None or machine.released_at is not None:
            raise ValidationError("machine is not available to suspend")
        if await self._repo.has_active_turn(machine):
            raise ConflictError(say("machineBusyCannotSuspend"))
        remote = await self._client.suspend_machine(machine.machine_id)
        return await self._repo.set_state(
            machine,
            status=_as_status(remote.get("status")),
            ip=remote.get("ip"),
            seen_at=datetime.now(UTC),
        )

    async def resume(self, machine: ProjectMachine) -> ProjectMachine:
        if machine.machine_id is None or machine.released_at is not None:
            raise ValidationError("machine is not available to resume")
        remote = await self._client.resume_machine(machine.machine_id)
        return await self._repo.set_state(
            machine,
            status=_as_status(remote.get("status")),
            ip=remote.get("ip"),
            seen_at=datetime.now(UTC),
        )

    # --- enrollment: making the machine an agent host -------------------

    async def enroll(self, machine: ProjectMachine) -> ProjectMachine:
        """Make this machine a cheese device, with nobody at a keyboard.

        The device flow exists for a human with a browser. Here the platform is
        the one that asked for the machine, so it mints the credential itself and
        writes it where `auth login` would have — then `link connect` finds the
        machine already logged in and just installs the service.
        """
        if machine.device_id:
            return machine
        if not machine.ip or not machine.bootstrap_key:
            raise ValidationError("machine is not ready to be enrolled")
        if machine.owner_user_id is None:
            raise ValidationError(
                "machine has no owner to enroll it for — it predates enrollment"
            )

        origin = settings.connector_public_base.rstrip("/")
        if not origin or "localhost" in origin or "127.0.0.1" in origin:
            # The machine has to reach this origin from its own network; a
            # localhost default would enroll a device that can never call home.
            raise ValidationError(
                "connector_public_base must be an origin the machine can reach"
            )

        code = await self._devices.start(f"{machine.hostname} (MicroCloud)")
        device = await self._devices.approve(
            code,
            owner_user_id=machine.owner_user_id,
            # 入口决定待遇 (#282 决定 2): the platform asked MicroCloud for this
            # machine, so the platform may reclaim it. A CONSTANT, never derived
            # from what the machine looks like — the identical VM enrolled by a
            # human through the connector is `self_hosted` and untouchable.
            supply=Supply.cloud,
            name=machine.hostname,
        )
        project = await self._projects.get(machine.project_id)
        if project is None:
            raise NotFoundError("project not found")
        if project.team_id is not None:
            # v4 ownership: enroll once into the team's compute pool. Every
            # project of that team can then select it without per-project rows.
            await self._devices.assign_to_team(
                device.device_id,
                project.team_id,
                actor_user_id=machine.owner_user_id,
            )
        else:
            # Compatibility for pre-personal-team project rows.
            await self._devices.assign_to_project(
                device.device_id,
                machine.project_id,
                actor_user_id=machine.owner_user_id,
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
        # `link connect` was answered 403 (unknown device token) and only the
        # connector's 2s retry saved the enrollment — two refusals before the
        # accept on 2026-09-02, machine 478.
        await self._session.commit()
        script = enrollment.bootstrap_script(
            origin=origin, token=device.token, device_id=device.device_id
        )

        async def progress(text: str) -> None:
            await startup_progress(machine.topic_id, text, machine_id=machine.id)

        await progress(
            say("cloudEnrollStarted", attempt=(machine.enroll_attempts or 0) + 1)
        )
        try:
            output = await enrollment.run_bootstrap(
                ip=machine.ip,
                login_user=machine.login_user,
                private_key=machine.bootstrap_key,
                script=script,
                progress=progress,
            )
        except enrollment.EnrollmentError as exc:
            # Never let the token reach a log line or an API error body.
            reason = enrollment.redact(str(exc), device.token)
            logger.warning("enrolling machine %s failed: %s", machine.hostname, reason)
            failure = (
                say("cloudEnrollTimedOut")
                if "timed out" in reason
                else say("cloudEnrollTransferFailed")
                if "transfer" in reason
                else say("cloudEnrollScriptFailed")
            )
            await startup_progress(
                machine.topic_id,
                say(
                    "cloudEnrollFailed",
                    failure=failure,
                    next=(
                        say("cloudEnrollGaveUp")
                        if (machine.enroll_attempts or 0) + 1 >= MAX_ENROLL_ATTEMPTS
                        else say("cloudEnrollWillRetry")
                    ),
                ),
                machine_id=machine.id,
                failed=(machine.enroll_attempts or 0) + 1 >= MAX_ENROLL_ATTEMPTS,
            )
            return await self._repo.mark_enroll_failed(machine, error=reason)

        await progress(say("cloudConnectorInstalled"))
        logger.info(
            "enrolled machine %s as device %s: %s",
            machine.hostname,
            device.device_id,
            enrollment.redact(output, device.token)[-200:],
        )
        return await self._repo.mark_enrolled(
            machine, device_id=device.device_id, when=datetime.now(UTC)
        )

    async def refresh_due(self, limit: int = 10) -> int:
        """Poll MicroCloud for machines still changing and for settled ones not
        re-checked within `microcloud_reconcile_interval_s`.

        This sweep is what keeps the table current; reads only report it.
        Enrolment and a room's lease wait on a machine settling (machine 473 sat
        unused for 13 minutes on 2026-08-14 while MicroCloud had it settled),
        and a settled machine destroyed upstream must stop counting against the
        project's limit (three did on 2026-08-02).
        """
        machines = await self._repo.list_due(
            limit,
            seen_before=datetime.now(UTC)
            - timedelta(seconds=settings.microcloud_reconcile_interval_s),
        )
        for machine in machines:
            try:
                if machine.status not in {MachineStatus.suspended, *GONE}:
                    await self.refresh(machine)
                if machine.status in GONE:
                    # Gone at the provider. Left alone, the row stays an
                    # unreleased lease on a machine that no longer exists.
                    await self.forget(machine)
                    continue
                if (
                    machine.status == MachineStatus.suspended
                    and machine.topic_id is not None
                ):
                    from app.domain.agent.chat import cloud_waiting_topics

                    if await cloud_waiting_topics(self._session, [machine.topic_id]):
                        await self.resume(machine)
            except MicroCloudError:
                # An unreachable provider is not this sweep's problem to solve;
                # the next tick tries again, and `refresh` records the attempt.
                logger.warning("refreshing machine %s failed", machine.hostname)
        return len(machines)

    async def enroll_pending(self, limit: int = 5) -> dict[str, int]:
        """Enroll every machine that is up but not yet a device.

        Runs on a clock rather than in a request: it SSHes into a machine,
        which is far too slow to hang a read on, and it must keep happening for a
        machine that became ready while nobody was looking.
        """
        machines = await self._repo.list_awaiting_enrollment(limit)
        enrolled = failed = 0
        for machine in machines:
            try:
                result = await self.enroll(machine)
            except Exception:  # one machine's failure must not stop the rest
                logger.exception("enrolling machine %s raised", machine.hostname)
                failed += 1
                continue
            if result.device_id:
                enrolled += 1
            else:
                failed += 1
        return {"enrolled": enrolled, "failed": failed}

    async def forget(self, machine: ProjectMachine) -> None:
        """Drop a vanished machine and the connector device enrolled for it."""
        if machine.device_id is not None and machine.owner_user_id is not None:
            device = await self._devices.get_device(machine.device_id)
            if device is not None and device.supply is not Supply.cloud:
                # Structurally impossible — a row in this table was provisioned by
                # `enroll`, which writes supply=cloud. Handled like the owner
                # mismatch below (loud, and the machine row still gets reaped)
                # rather than by letting `delete_platform_provisioned` raise:
                # `forget` runs from `list_for_project`, a GET path, so an
                # exception here would wedge machine listing for the whole project
                # over one bad row. Refusing to delete is the safe direction; the
                # raise stays where it protects NEW reclaim paths.
                logger.error(
                    "not deleting device %s for machine %s: supply=%s, not cloud "
                    "— the platform does not destroy machines it did not open",
                    machine.device_id,
                    machine.hostname,
                    device.supply,
                )
            elif device is not None and device.owner_user_id == machine.owner_user_id:
                # Device deletion also removes project/team/topic bindings. Do
                # this before the machine row so a failure remains retryable.
                await self._devices.delete_platform_provisioned(
                    machine.device_id, actor_user_id=machine.owner_user_id
                )
            elif device is not None:
                # Never delete a device now owned by somebody else. This should
                # be impossible for platform-enrolled machines, so retain an
                # operator-visible signal if historical data disagrees.
                logger.error(
                    "not deleting device %s for machine %s: owner mismatch",
                    machine.device_id,
                    machine.hostname,
                )
        await self._repo.delete(machine)


def _stale(machine: ProjectMachine) -> bool:
    """Whether a SETTLED machine is due to be re-checked against MicroCloud.

    Settled used to mean "never asked again", which made this table unable to
    notice a machine the provider had destroyed. Observed live on 2026-08-02:
    three machines reported `running` and enrolled here while MicroCloud 404'd
    every one of them. That is not only a wrong reading — `provision()` counts
    those rows against the per-project limit, so a project whose machines are
    gone upstream can never get another one.

    Bounded rather than every-read: this sits on a request path, and a provider
    round-trip per machine per page load is its own outage waiting to happen.
    """
    seen = machine.last_seen_at
    if seen is None:
        return True
    if seen.tzinfo is None:  # rows written before the column existed
        seen = seen.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - seen).total_seconds()
    return age >= settings.microcloud_reconcile_interval_s


def _still_moving(machine: ProjectMachine) -> bool:
    """Whether either lifecycle can still change on its own.

    Both must be considered: a room's lease waits on both, and MicroCloud can
    report them settling at different moments, so polling on the machine
    status alone would freeze whichever `ai_status` was read first.
    """
    return (
        machine.status in TRANSITIONAL
        or machine.status == MachineStatus.unknown
        or machine.ai_status in AI_TRANSITIONAL
        or machine.ai_status == AiStatus.unknown
    )


def _as_status(value: object) -> MachineStatus:
    """MicroCloud is the source of truth for status, but an unrecognised value
    must not blow up a read — a newer provider status maps to `unknown`."""
    try:
        return MachineStatus(str(value))
    except ValueError:
        return MachineStatus.unknown


def _as_ai_status(value: object) -> AiStatus:
    """Same tolerance as _as_status: MicroCloud may gain an AI state we don't
    know yet, and that must not break reading a machine."""
    if value is None:
        return AiStatus.unknown
    try:
        return AiStatus(str(value))
    except ValueError:
        return AiStatus.unknown


async def left_unpushed_on(db, topic_id, device_id) -> bool:
    """Another session of the room left this machine without pushing: its work
    is only there, so the machine waits for the room's cleanup."""
    from app.domain.agent_session.models import AgentSession

    if device_id is None:
        return False
    requests = await db.scalars(
        select(AgentSession.execution_request).where(
            AgentSession.topic_id == topic_id,
            AgentSession.execution_request.is_not(None),
        )
    )
    return any(
        lease.get("device_id") == device_id
        for request in requests
        for lease in request.get("retained_leases", [])
    )
