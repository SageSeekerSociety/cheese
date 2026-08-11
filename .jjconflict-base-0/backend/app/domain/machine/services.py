"""Project machine business logic.

Gives a project real compute: one call provisions a Debian machine from
MicroCloud, bills it to a fund account owned by that project, and hands back an
address someone can SSH into. Provisioning is asynchronous on MicroCloud's side,
so this service never blocks on it — status is refreshed whenever a machine is
read, which also means a machine that finished (or failed) while nobody was
looking is correct the next time anyone asks.
"""

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.machine import enrollment
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
from app.domain.project.repositories import ProjectRepository

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


class MachineService:
    def __init__(
        self, session: AsyncSession, client: MicroCloudClient | None = None
    ) -> None:
        self._session = session
        self._repo = ProjectMachineRepository(session)
        self._projects = ProjectRepository(session)
        self._client = client or MicroCloudClient()
        self._devices = DeviceService(SqlDeviceRepository(session))

    @property
    def available(self) -> bool:
        return self._client.configured

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

        offering = await self._pick_offering()

        def clamp(value: int | None, default: int, lo: str, hi: str) -> int:
            # The allowed range is per-offering, so a spec is only meaningful
            # against the offering we actually landed on.
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

        # Only machines that still exist count. A destroyed one lingers as a row
        # until a later read confirms MicroCloud has forgotten it, and counting
        # those would make a project's slots impossible to reclaim — delete then
        # create would be refused for a machine that is already gone.
        existing = [
            m
            for m in await self._repo.list_for_project(project_id)
            if m.status not in GONE
        ]
        if len(existing) >= settings.microcloud_max_machines_per_project:
            raise ValidationError(
                "this project already has "
                f"{settings.microcloud_max_machines_per_project} machine(s); "
                "delete one before provisioning another"
            )
        hostname = derive_hostname(project.name, project_id, len(existing) + 1)

        customer_id, account_id = await self._ensure_account(project_id)
        user = login_user or settings.microcloud_login_user

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
        # The platform needs its own way in to enroll the machine later, and the
        # human must not lose theirs by us taking the single key slot: both are
        # authorised, one per line, which is what authorized_keys is.
        bootstrap_private, bootstrap_public = await enrollment.generate_keypair()
        authorized = enrollment.combine_authorized_keys(bootstrap_public, ssh_pubkey)
        if authorized:
            body["sshPubkey"] = authorized

        created = await self._client.create_machine(body)
        created = await self._apply_desired_ai_mode(created)
        return await self._repo.add(
            project_id=project_id,
            machine_id=int(created["id"]),
            customer_id=customer_id,
            account_id=account_id,
            offering_id=int(offering["id"]),
            hostname=hostname,
            login_user=user,
            cores=spec["cores"],
            memory_mb=spec["memoryMb"],
            disk_gb=spec["diskGb"],
            status=_as_status(created.get("status")),
            ip=created.get("ip"),
            requested_by=requested_by,
            ai_mode=str(created.get("aiMode") or "none"),
            ai_status=_as_ai_status(created.get("aiStatus")),
            owner_user_id=owner_user_id,
            bootstrap_key=bootstrap_private,
        )

    async def _apply_desired_ai_mode(self, created: dict) -> dict:
        """Switch a fresh machine's built-in AI channel to the configured mode.

        MicroCloud provisions on newapi, whose default routes to a cheap
        non-Claude model — the operator guidance is ccproxy (the console's
        →ccproxy button). Best-effort: a failure here must not fail the
        provision, and the enrollment sweep reconciles stragglers."""
        desired = (settings.microcloud_ai_mode or "").strip().lower()
        current = str(created.get("aiMode") or "").lower()
        if not desired or current == desired:
            return created
        try:
            switched = await self._client.switch_ai(int(created["id"]), desired)
        except Exception:  # noqa: BLE001 — the sweep retries; provision must land
            logger.warning(
                "switching machine %s AI channel to %s failed — the enrollment "
                "sweep will retry",
                created.get("id"),
                desired,
            )
            return created
        return {
            **created,
            "aiMode": str(switched.get("aiMode") or desired).lower(),
            "aiStatus": str(switched.get("aiStatus") or "provisioning").lower(),
        }

    async def reconcile_ai_mode(self, limit: int = 5) -> int:
        """Level-triggered half of the →ccproxy story: any settled machine on
        the wrong built-in AI channel gets switched. Catches machines whose
        provision-time switch failed or raced MicroCloud's own wiring, and
        machines that predate the setting."""
        desired = (settings.microcloud_ai_mode or "").strip().lower()
        if not desired:
            return 0
        machines = await self._repo.list_ai_mode_mismatch(desired, limit)
        switched = 0
        for machine in machines:
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
            code, owner_user_id=machine.owner_user_id, name=machine.hostname
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

        logger.info(
            "enrolled machine %s as device %s: %s",
            machine.hostname,
            device.device_id,
            enrollment.redact(output, device.token)[-200:],
        )
        return await self._repo.mark_enrolled(
            machine, device_id=device.device_id, when=datetime.now(UTC)
        )

    async def enroll_pending(self, limit: int = 5) -> dict[str, int]:
        """Enroll every machine that is up and wired but not yet a device.

        Runs on the scheduler rather than in a request: it SSHes into a machine,
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
            if device is not None and device.owner_user_id == machine.owner_user_id:
                # Device deletion also removes project/team/topic bindings. Do
                # this before the machine row so a failure remains retryable.
                await self._devices.delete_owned(
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
