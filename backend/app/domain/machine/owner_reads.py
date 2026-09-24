"""Column-limited Cloud ownership reads for the long-lived device owner."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.machine.models import ProjectMachine


async def active_cloud_device_for_project(
    session: AsyncSession, device_id: str, project_id: uuid.UUID
) -> bool:
    return (
        await session.scalar(
            select(ProjectMachine.device_id)
            .join(DeviceRow, DeviceRow.device_id == ProjectMachine.device_id)
            .where(
                ProjectMachine.device_id == device_id,
                ProjectMachine.project_id == project_id,
                ProjectMachine.released_at.is_(None),
                ProjectMachine.superseded_at.is_(None),
                DeviceRow.supply == Supply.cloud,
            )
        )
        is not None
    )
