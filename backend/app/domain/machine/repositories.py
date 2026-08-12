"""Project machine data access."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.machine.models import (
    MAX_ENROLL_ATTEMPTS,
    AiStatus,
    MachineStatus,
    ProjectMachine,
)


class ProjectMachineRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        machine_id: int,
        customer_id: int,
        account_id: int,
        offering_id: int,
        hostname: str,
        login_user: str,
        cores: int,
        memory_mb: int,
        disk_gb: int,
        status: MachineStatus,
        ip: str | None,
        requested_by: str | None,
        ai_mode: str = "none",
        ai_status: AiStatus = AiStatus.unknown,
        owner_user_id: int | None = None,
        bootstrap_key: str | None = None,
    ) -> ProjectMachine:
        machine = ProjectMachine(
            project_id=project_id,
            machine_id=machine_id,
            customer_id=customer_id,
            account_id=account_id,
            offering_id=offering_id,
            hostname=hostname,
            login_user=login_user,
            cores=cores,
            memory_mb=memory_mb,
            disk_gb=disk_gb,
            status=status,
            ip=ip,
            requested_by=requested_by,
            ai_mode=ai_mode,
            ai_status=ai_status,
            owner_user_id=owner_user_id,
            bootstrap_key=bootstrap_key,
        )
        self._session.add(machine)
        await self._session.flush()
        await self._session.refresh(machine)
        return machine

    async def get(self, machine_row_id: uuid.UUID) -> ProjectMachine | None:
        return await self._session.get(ProjectMachine, machine_row_id)

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMachine]:
        result = await self._session.execute(
            select(ProjectMachine)
            .where(ProjectMachine.project_id == project_id)
            .order_by(ProjectMachine.created_at)
        )
        return list(result.scalars())

    async def find_by_hostname(
        self, project_id: uuid.UUID, hostname: str
    ) -> ProjectMachine | None:
        result = await self._session.execute(
            select(ProjectMachine).where(
                ProjectMachine.project_id == project_id,
                ProjectMachine.hostname == hostname,
            )
        )
        return result.scalars().first()

    async def set_state(
        self,
        machine: ProjectMachine,
        *,
        status: MachineStatus,
        ip: str | None,
        ai_mode: str | None = None,
        ai_status: AiStatus | None = None,
        seen_at: datetime | None = None,
    ) -> ProjectMachine:
        machine.status = status
        if ai_mode is not None:
            machine.ai_mode = ai_mode
        if ai_status is not None:
            machine.ai_status = ai_status
        if seen_at is not None:
            machine.last_seen_at = seen_at
        # Never blank an IP we already learned: a transient read that omits it
        # would otherwise erase the only way back into a running machine.
        if ip:
            machine.ip = ip
        await self._session.flush()
        return machine

    async def touch_seen(
        self, machine: ProjectMachine, *, when: datetime
    ) -> ProjectMachine:
        """Record that MicroCloud was asked, without claiming to have learned
        anything. Used when the provider was unreachable and the machine's last
        known state is still the best answer we have."""
        machine.last_seen_at = when
        await self._session.flush()
        return machine

    async def delete(self, machine: ProjectMachine) -> None:
        await self._session.delete(machine)
        await self._session.flush()

    async def mark_enrolled(
        self, machine: ProjectMachine, *, device_id: str, when: datetime
    ) -> ProjectMachine:
        machine.device_id = device_id
        machine.enrolled_at = when
        machine.enroll_error = None
        # The bootstrap key existed for this one setup; keeping it would leave a
        # standing way into the machine that nobody asked for.
        machine.bootstrap_key = None
        await self._session.flush()
        return machine

    async def mark_enroll_failed(
        self, machine: ProjectMachine, *, error: str
    ) -> ProjectMachine:
        machine.enroll_error = error[:1000]
        machine.enroll_attempts = (machine.enroll_attempts or 0) + 1
        await self._session.flush()
        return machine

    async def list_awaiting_enrollment(self, limit: int) -> list[ProjectMachine]:
        """Machines that are up, have their agent access wired, and are not yet
        enrolled — and that we have not already given up on."""
        result = await self._session.execute(
            select(ProjectMachine)
            .where(
                ProjectMachine.device_id.is_(None),
                ProjectMachine.status == MachineStatus.running,
                ProjectMachine.ai_status == AiStatus.ready,
                ProjectMachine.bootstrap_key.is_not(None),
                ProjectMachine.ip.is_not(None),
                ProjectMachine.enroll_attempts < MAX_ENROLL_ATTEMPTS,
            )
            .order_by(ProjectMachine.created_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def list_ai_mode_mismatch(
        self, desired: str, limit: int
    ) -> list[ProjectMachine]:
        """Machines whose built-in AI channel settled on the wrong mode.

        Only settled machines (running + ai ready) are candidates — a machine
        still provisioning will be judged when it lands, and fighting a
        transitional state would race MicroCloud's own wiring."""
        result = await self._session.execute(
            select(ProjectMachine)
            .where(
                ProjectMachine.status == MachineStatus.running,
                ProjectMachine.ai_status == AiStatus.ready,
                ProjectMachine.ai_mode != desired,
            )
            .order_by(ProjectMachine.created_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def is_provisioned_device(self, device_id: str) -> bool:
        """Whether this device is a machine cheese provisioned from MicroCloud.

        Such a machine is on its OWN host by definition — it can never share this
        backend's filesystem, whatever the deployment-wide co-location setting
        says.
        """
        result = await self._session.execute(
            select(ProjectMachine.id).where(ProjectMachine.device_id == device_id)
        )
        return result.first() is not None
