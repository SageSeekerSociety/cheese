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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.domain.agent.compute_configs import room_choice
from app.domain.device.ccproxy_tenant import CcproxyTenantError
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.machine import enrollment
from app.domain.machine.limits import get_machine_limit
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError
from app.domain.machine.models import (
    AI_TRANSITIONAL,
    GONE,
    TRANSITIONAL,
    AiStatus,
    MachineStatus,
    ProjectMachine,
)
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.membership.services import MemberService
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


# How long a machine may sit at the wrong AI mode before it is enrolled anyway.
# Long enough that a normal switch (seconds) always wins the race, short enough
# that a machine whose channel is genuinely stuck still becomes usable compute
# within one coffee. It trades the per-machine ccproxy identity — a fallback the
# meter already handles — for never leaving a healthy machine unenrolled.
ENROLL_SETTLE_GRACE = timedelta(minutes=10)
# One provider create per room per process: a second admission of the room
# waits here, holding no database lock, until the first has recorded the
# machine. One lock per room ever provisioned, so this stays small.
_create_locks: dict[uuid.UUID, asyncio.Lock] = {}


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

    async def require_team_create_authority(
        self, team_id: int, actor: Actor, *, conceal_nonmember: bool = False
    ) -> None:
        """Apply the paid machine-create rule to a team-scoped Cloud choice."""
        if not actor.authenticated or actor.user_id is None:
            raise AuthenticationRequiredError("Login required to create cloud machines")
        teams = team_service(self._session)
        if not await teams.is_team_member(team_id, actor.user_id):
            if conceal_nonmember:
                raise NotFoundError("Project not found")
            raise ForbiddenError(
                "Only team owners and admins can create cloud machines"
            )
        if not await teams.is_team_at_least_admin(team_id, actor.user_id):
            raise ForbiddenError(
                "Only team owners and admins can create cloud machines"
            )

    async def require_create_authority(
        self, project_id: uuid.UUID, actor: Actor
    ) -> None:
        """The one authorization rule for every path that can create a billed VM."""
        if not actor.authenticated:
            raise AuthenticationRequiredError("Login required to create cloud machines")
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        team_id = await self._projects.team_for_project(project_id)
        if team_id is not None:
            await self.require_team_create_authority(
                team_id, actor, conceal_nonmember=True
            )
            return
        # Legacy team-less project. An outsider must not learn that it exists, let
        # alone that it has a machine inventory — so a non-member is concealed as
        # 404 here, the same as the team branch above. `require_manager` alone
        # answers 403, which is right for roster writes (you can see the project,
        # you just may not manage it) and wrong here.
        members = MemberService(self._session)
        if project.owner_handle != actor.handle:
            roster, _ = await members.list_for_project(project_id)
            if not any(m.user_handle == actor.handle for m in roster):
                raise NotFoundError("Project not found")
        await members.require_manager(project_id, actor)

    async def require_use_authority(self, project_id: uuid.UUID, actor: Actor) -> None:
        """Team membership authorizes room execution within the team's quota.

        Membership is the whole question. `TeamUserRelation` is (team, user) with
        no human/agent distinction, so an agent seated on a team is as entitled
        to the team's machines as anyone else on it. What is still required is an
        id to check that membership against.
        """
        if actor.via != "token" or actor.user_id is None:
            raise AuthenticationRequiredError("Login required to use cloud compute")
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        team_id = await self._projects.team_for_project(project_id)
        if team_id is not None:
            if not await team_service(self._session).is_team_member(
                team_id, actor.user_id
            ):
                raise ForbiddenError("只有团队成员可以使用团队云额度")
        else:
            await MemberService(self._session).require_manager(project_id, actor)

    async def _pick_offering(self) -> dict:
        offerings = await self._client.list_offerings()
        if not offerings:
            raise ValidationError(
                "MicroCloud has granted this deployment no offering — an operator "
                "must grant one before machines can be created"
            )
        wanted = settings.microcloud_offering_id
        if wanted:
            for offering in offerings:
                if int(offering["id"]) == wanted:
                    return offering
            raise ValidationError(f"configured offering {wanted} is not granted")
        active = [o for o in offerings if o.get("status") == "active"]
        return (active or offerings)[0]

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
        topic_id: uuid.UUID | None = None,
        requested_by: str | None,
        ssh_pubkey: str | None = None,
        owner_user_id: int | None = None,
        login_user: str | None = None,
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
        offering = await self._pick_offering()

        def clamp(value: int | None, default: int, lo: str, hi: str) -> int:
            # The allowed range is per-offering, so a spec is only meaningful
            # against the offering we actually landed on.
            if value is not None and not int(offering[lo]) <= value <= int(
                offering[hi]
            ):
                raise ValidationError("所选云配置超出当前供应范围，请选择其他配置")
            return max(int(offering[lo]), min(int(offering[hi]), int(value or default)))

        spec = {
            "cores": clamp(
                cores, settings.microcloud_default_cores, "coresMin", "coresMax"
            ),
            "memoryMb": clamp(
                memory_mb,
                settings.microcloud_default_memory_mb,
                "memoryMbMin",
                "memoryMbMax",
            ),
            "diskGb": clamp(
                disk_gb, settings.microcloud_default_disk_gb, "diskGbMin", "diskGbMax"
            ),
        }

        customer_id, account_id = await self._ensure_account(project_id)
        user = login_user or settings.microcloud_login_user

        await self._repo.lock_team_quota(team_id)
        existing = await self.quota_machines(team_id)
        limit = await get_machine_limit(self._session, team_id)
        if len(existing) >= limit:
            raise ValidationError(
                f"团队云虚拟机已使用 {len(existing)} / {limit} 台，"
                "请先释放不再使用的机器"
            )
        project_used = sum(m.project_id == project_id for m in existing)
        hostname = derive_hostname(project.name, project_id, project_used + 1)

        body = {
            "customerId": customer_id,
            "accountId": account_id,
            # MicroCloud defaults both to accountId. Naming them is what makes
            # it possible to split compute spend from AI spend later without
            # re-provisioning every machine.
            "newapiAccountId": account_id,
            "ccproxyAccountId": account_id,
            "hostname": hostname,
            "offeringId": int(offering["id"]),
            "user": user,
            **spec,
        }
        # Ask for the AI channel at create (micro-cloud#78): a machine born on
        # ccproxy starts its subscription login the moment it runs, instead of
        # being set up on newapi first and switched by our sweep — one
        # provisioning of the channel rather than two. A MicroCloud that
        # predates the field ignores it and the sweep switches as before.
        desired_ai_mode = settings.cloud_executor_ai_mode
        if desired_ai_mode:
            body["aiMode"] = desired_ai_mode
        if topic_id is not None and owner_user_id is not None and not ssh_pubkey:
            warm = await self._warm_pool.reserve(
                body=body,
                project_id=project_id,
                topic_id=topic_id,
                requested_by=requested_by,
                owner_user_id=owner_user_id,
            )
            if warm is not None:
                return warm
        # The platform needs its own way in to enroll the machine later, and the
        # human must not lose theirs by us taking the single key slot: both are
        # authorised, one per line, which is what authorized_keys is.
        bootstrap_private, bootstrap_public = await enrollment.generate_keypair()
        # The operator's key too: the bootstrap key is erased at enrollment, and
        # a machine nobody can log into cannot be diagnosed (see the setting).
        authorized = enrollment.combine_authorized_keys(
            bootstrap_public, ssh_pubkey, settings.microcloud_operator_ssh_pubkey
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
            ai_mode=desired_ai_mode or "none",
            ai_status=AiStatus.unknown,
            owner_user_id=owner_user_id,
            bootstrap_key=bootstrap_private,
        )
        creating = (
            _create_locks.setdefault(topic_id, asyncio.Lock())
            if topic_id is not None
            else asyncio.Lock()
        )
        async with creating:
            await self._session.commit()
            # No separate switch call here. MicroCloud answers 400 to a switch on
            # a machine that is still provisioning, so asking right after create
            # only cost the turn path a 22s refusal (measured 2026-09-02, machine
            # 478). The mode rides in the create body above; `reconcile_ai_mode`
            # still switches a machine that came up on the wrong channel.
            try:
                created = await self._client.create_machine(body)
            except BaseException:
                # Nothing was created, so nothing is owed: give the slot back.
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
            raise ValidationError("当前房间未选择云端配置")
        topic.compute_config = choice.model_dump()
        topic.compute_profile = "cloud"
        agent = await IdentityService(self._session).ensure_topic_agent_user(topic_id)
        return await self.provision(
            project_id=topic.project_id,
            topic_id=topic_id,
            requested_by=actor.handle,
            owner_user_id=agent.id,
            cores=choice.cores,
            memory_mb=choice.memory_mb,
            disk_gb=choice.disk_gb,
        )

    async def topic_machine(self, topic_id: uuid.UUID) -> ProjectMachine | None:
        return await self._repo.get_active_for_topic(topic_id)

    async def detach_archived_machine(self, topic_id: uuid.UUID) -> None:
        """Reopening gets new compute; the recorded cleanup still owns the old VM."""
        await self._repo.lock_topic(topic_id)
        machine = await self._repo.get_active_for_topic(topic_id)
        if machine is None:
            return
        binding = await self._devices.topic_binding(topic_id)
        if binding is not None and binding.device_id == machine.device_id:
            await self._devices.release_topic_device(
                topic_id, reason="reopen after cleanup claim"
            )
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

    async def reconcile_ai_mode(self, limit: int = 5) -> int:
        """Level-triggered half of the →ccproxy story: any settled machine on
        the wrong built-in AI channel gets switched. Catches machines whose
        provision-time switch failed or raced MicroCloud's own wiring, and
        machines that predate the setting."""
        if settings.agent_session_device_id:
            return 0
        desired = settings.cloud_executor_ai_mode
        if not desired:
            return 0
        machines = await self._repo.list_ai_mode_mismatch(desired, limit)
        switched = 0
        for machine in machines:
            assert machine.machine_id is not None  # the query excludes reservations
            try:
                result = await self._client.switch_ai(machine.machine_id, desired)
            except MicroCloudError:
                logger.warning(
                    "switching machine %s to %s failed", machine.hostname, desired
                )
                continue
            await self._repo.set_state(
                machine,
                status=machine.status,
                ip=machine.ip,
                ai_mode=str(result.get("aiMode") or desired).lower(),
                ai_status=_as_ai_status(str(result.get("aiStatus") or "").lower()),
            )
            switched += 1
        if switched:
            logger.info("AI channel reconcile: %s machine(s) → %s", switched, desired)
        return switched

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
            # putting a 30s timeout on every read.
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
        team_id = project.team_id
        if team_id is None:
            team_id = await self._projects.team_for_project(project_id)
        if team_id is None:
            raise ValidationError("请先将项目关联到团队，再分配云资源")
        return team_id

    async def quota_machines(self, team_id: int) -> list[ProjectMachine]:
        """Inventory counted by both admission and the allocation notice."""
        return [
            m
            for m in await self._repo.list_for_team(team_id)
            if m.status not in GONE and m.released_at is None
        ]

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMachine]:
        machines = await self._repo.list_for_project(project_id)
        alive: list[ProjectMachine] = []
        for machine in machines:
            if _still_moving(machine) or _stale(machine):
                await self.refresh(machine)
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
            # #358: a fresh, disposable, one-per-topic VM IS its own empty box —
            # "see the whole machine" adds no capability there, so the visibility
            # axis collapses and `host` is both correct and safe (there are no
            # other rooms or pre-existing services to reach). The gate that refuses
            # `isolated` (no transport yet) must never fire for cloud compute, so
            # this is `host`, not the connector's isolated default.
            visibility=Visibility.host,
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
        try:
            output = await enrollment.run_bootstrap(
                ip=machine.ip,
                login_user=machine.login_user,
                private_key=machine.bootstrap_key,
                script=script,
            )
        except enrollment.EnrollmentError as exc:
            # Never let the token reach a log line or an API error body.
            reason = enrollment.redact(str(exc), device.token)
            logger.warning("enrolling machine %s failed: %s", machine.hostname, reason)
            return await self._repo.mark_enroll_failed(machine, error=reason)

        # Read here or never: `mark_enrolled` erases the bootstrap key, so this
        # is the last moment the platform can look at the machine over ssh.
        upstream = enrollment.parse_ccproxy_upstream(output)
        logger.info(
            "enrolled machine %s as device %s (ccproxy identity %s): %s",
            machine.hostname,
            device.device_id,
            upstream.split(":", 1)[0] if upstream else "not recorded",
            # The identity's password rides in the same output as the device
            # token, so it is redacted on the same line rather than one call
            # later — a log is exactly where a credential must not appear.
            enrollment.redact(output, device.token, upstream or "")[-200:],
        )
        return await self._repo.mark_enrolled(
            machine,
            device_id=device.device_id,
            when=datetime.now(UTC),
            ccproxy_upstream=upstream,
        )

    async def refresh_unsettled(self, limit: int = 10) -> int:
        """Poll MicroCloud for machines whose lifecycle can still change.

        Nothing else does this outside a read: `list_for_project` refreshes what
        it returns, so a machine that settles while nobody has the project open
        keeps its last-read state forever. That was harmless while enrolment
        only needed `running`; it stopped being harmless once enrolment also
        waits for the AI channel, because the value it waits on is exactly the
        one that goes stale. Machine 473 sat unenrolled for 13 minutes on
        2026-08-14 while MicroCloud had it `ready` throughout.
        """
        machines = await self._repo.list_unsettled(limit)
        for machine in machines:
            try:
                await self.refresh(machine)
            except MicroCloudError:
                # An unreachable provider is not this sweep's problem to solve;
                # the next tick tries again, and `refresh` records the attempt.
                logger.warning("refreshing machine %s failed", machine.hostname)
        return len(machines)

    async def enroll_pending(self, limit: int = 5) -> dict[str, int]:
        """Enroll every machine that is up and wired but not yet a device.

        Runs on the scheduler rather than in a request: it SSHes into a machine,
        which is far too slow to hang a read on, and it must keep happening for a
        machine that became ready while nobody was looking.
        """
        machines = await self._repo.list_awaiting_enrollment(
            limit,
            desired_ai_mode=settings.cloud_executor_ai_mode,
            settle_cutoff=datetime.now(UTC) - ENROLL_SETTLE_GRACE,
        )
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
                try:
                    await self._devices.delete_platform_provisioned(
                        machine.device_id, actor_user_id=machine.owner_user_id
                    )
                except CcproxyTenantError as exc:
                    # #420: the device carries a ccproxy ticket and revocation
                    # was not confirmed. `forget` runs from `list_for_project`
                    # (a GET), so raising here would wedge machine listing for
                    # the whole project over a ccproxy outage. Keep BOTH rows —
                    # the machine row is what brings us back here to retry once
                    # ccproxy answers again — and say so loudly.
                    logger.error(
                        "not reaping machine %s yet: ccproxy revocation for "
                        "device %s unconfirmed (%s)",
                        machine.hostname,
                        machine.device_id,
                        exc,
                    )
                    return
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

    Both must be considered. A machine reaches `running` while MicroCloud is
    still wiring its Claude Code, so polling on the machine status alone would
    stop the moment it settles and freeze `ai_status` at `provisioning` forever
    — reporting a machine that can't run a turn as if it were finished.
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
