"""The computers a project's local MCP servers run on, as `remote_mcp.local`
asks about them: whose each one is, and whether it is a member's own — whose
owner is asked before a command of the project's runs on it — or one of the
platform's, which asks nobody."""

import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import DeviceProjectRow, DeviceRow, DeviceTeamRow
from app.domain.device.supply import Supply
from app.domain.project.models import Project
from app.domain.remote_mcp.local import Machine
from app.domain.user.models import User


async def owners(db: AsyncSession, device_ids: Sequence[str]) -> dict[str, str]:
    """Each device id to its owner's handle."""
    if not device_ids:
        return {}
    found = await db.execute(
        select(DeviceRow.device_id, User.username)
        .join(User, User.id == DeviceRow.owner_user_id)
        .where(DeviceRow.device_id.in_(list(device_ids)))
    )
    return {device_id: handle for device_id, handle in found}


async def names(db: AsyncSession, device_ids: Sequence[str]) -> dict[str, str]:
    """Each device id to the name its owner gave it."""
    if not device_ids:
        return {}
    found = await db.execute(
        select(DeviceRow.device_id, DeviceRow.name).where(
            DeviceRow.device_id.in_(list(device_ids))
        )
    )
    return {device_id: name for device_id, name in found}


async def owned(db: AsyncSession, handle: str) -> list[str]:
    """The devices this person owns."""
    found = await db.scalars(
        select(DeviceRow.device_id)
        .join(User, User.id == DeviceRow.owner_user_id)
        .where(User.username == handle)
    )
    return list(found)


async def machine(
    db: AsyncSession, project_id: uuid.UUID, device_id: str
) -> Machine | None:
    """The device, when the project may run on it: assigned to the project, or
    bound to the project's team. None for any other id."""
    team = await db.scalar(select(Project.team_id).where(Project.id == project_id))
    found = await db.execute(
        select(DeviceRow.device_id, DeviceRow.supply, User.username)
        .join(User, User.id == DeviceRow.owner_user_id)
        .where(
            DeviceRow.device_id == device_id,
            or_(
                DeviceRow.device_id.in_(
                    select(DeviceProjectRow.device_id).where(
                        DeviceProjectRow.project_id == project_id
                    )
                ),
                DeviceRow.device_id.in_(
                    select(DeviceTeamRow.device_id).where(DeviceTeamRow.team_id == team)
                ),
            ),
        )
    )
    row = found.first()
    if row is None:
        return None
    return Machine(device_id=row[0], owner=row[2], member=row[1] is not Supply.cloud)
