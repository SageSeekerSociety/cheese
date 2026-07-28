"""Project machine data access."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.machine.models import AiStatus, MachineStatus, ProjectMachine


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
    ) -> ProjectMachine:
        machine.status = status
        if ai_mode is not None:
            machine.ai_mode = ai_mode
        if ai_status is not None:
            machine.ai_status = ai_status
        # Never blank an IP we already learned: a transient read that omits it
        # would otherwise erase the only way back into a running machine.
        if ip:
            machine.ip = ip
        await self._session.flush()
        return machine

    async def delete(self, machine: ProjectMachine) -> None:
        await self._session.delete(machine)
        await self._session.flush()
