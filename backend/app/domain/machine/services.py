"""Project machine business logic.

Gives a project real compute: one call provisions a Debian machine from
MicroCloud, bills it to a fund account owned by that project, and hands back an
address someone can SSH into. Provisioning is asynchronous on MicroCloud's side,
so this service never blocks on it — status is refreshed whenever a machine is
read, which also means a machine that finished (or failed) while nobody was
looking is correct the next time anyone asks.
"""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError
from app.domain.machine.models import (
    AI_TRANSITIONAL,
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

        existing = await self._repo.list_for_project(project_id)
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
        if ssh_pubkey:
            body["sshPubkey"] = ssh_pubkey.strip()

        created = await self._client.create_machine(body)
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
        )

    async def refresh(self, machine: ProjectMachine) -> ProjectMachine:
        """Bring one row in line with MicroCloud. Never raises for a provider
        problem: a machine we can't reach is reported `unknown`, not lost."""
        try:
            remote = await self._client.get_machine(machine.machine_id)
        except MicroCloudError:
            return await self._repo.set_state(
                machine,
                status=MachineStatus.unknown,
                ip=None,
                ai_status=AiStatus.unknown,
            )
        if remote is None:
            return await self._repo.set_state(
                machine, status=MachineStatus.deleted, ip=None
            )
        return await self._repo.set_state(
            machine,
            status=_as_status(remote.get("status")),
            ip=remote.get("ip"),
            ai_mode=str(remote.get("aiMode") or machine.ai_mode),
            ai_status=_as_ai_status(remote.get("aiStatus")),
        )

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMachine]:
        machines = await self._repo.list_for_project(project_id)
        for machine in machines:
            if _still_moving(machine):
                await self.refresh(machine)
        return machines

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

    async def forget(self, machine: ProjectMachine) -> None:
        """Drop the row once MicroCloud no longer has the machine."""
        await self._repo.delete(machine)


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
