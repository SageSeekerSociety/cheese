"""SQL-backed ``DeviceRepository`` (production). Converts rows ↔ dataclasses so the
service stays storage-agnostic. Same contract as ``InMemoryDeviceRepository``."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import (
    DeviceAuthCodeRow,
    DeviceProjectRow,
    DeviceRow,
    DeviceTopicRow,
)
from app.domain.device.repository import AuthCode, Device
from app.domain.project.models import Project
from app.domain.team.models import TeamUserRelation


def _aware(dt: datetime) -> datetime:
    """Coerce a stored timestamp to timezone-aware UTC. Postgres TIMESTAMPTZ returns
    aware datetimes, but SQLite (tests) returns naive — the domain is always aware
    (project datetime rule), so normalize on read."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SqlDeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- codes -------------------------------------------------------------

    async def save_code(self, code: AuthCode) -> None:
        row = await self._session.get(DeviceAuthCodeRow, code.code)
        if row is None:
            row = DeviceAuthCodeRow(code=code.code, created_at=code.created_at)
            self._session.add(row)
        row.device_name = code.device_name
        row.status = code.status
        row.device_id = code.device_id
        await self._session.flush()

    async def get_code(self, code: str) -> AuthCode | None:
        row = await self._session.get(DeviceAuthCodeRow, code)
        if row is None:
            return None
        return AuthCode(
            code=row.code,
            device_name=row.device_name,
            status=row.status,
            created_at=_aware(row.created_at),
            device_id=row.device_id,
        )

    # -- devices -----------------------------------------------------------

    async def save_device(self, device: Device) -> None:
        row = await self._session.get(DeviceRow, device.device_id)
        if row is None:
            row = DeviceRow(device_id=device.device_id, created_at=device.created_at)
            self._session.add(row)
        row.name = device.name
        row.token = device.token
        row.owner_user_id = device.owner_user_id
        await self._session.flush()

    async def _to_device(self, row: DeviceRow) -> Device:
        project_ids = list(
            (
                await self._session.scalars(
                    select(DeviceProjectRow.project_id).where(
                        DeviceProjectRow.device_id == row.device_id
                    )
                )
            ).all()
        )
        return Device(
            device_id=row.device_id,
            name=row.name,
            token=row.token,
            owner_user_id=row.owner_user_id,
            created_at=_aware(row.created_at),
            project_ids=project_ids,
        )

    async def get_device(self, device_id: str) -> Device | None:
        row = await self._session.get(DeviceRow, device_id)
        return await self._to_device(row) if row is not None else None

    async def get_device_by_token(self, token: str) -> Device | None:
        row = await self._session.scalar(
            select(DeviceRow).where(DeviceRow.token == token)
        )
        return await self._to_device(row) if row is not None else None

    async def delete_device(self, device_id: str) -> None:
        await self._session.execute(
            delete(DeviceProjectRow).where(DeviceProjectRow.device_id == device_id)
        )
        await self._session.execute(
            delete(DeviceTopicRow).where(DeviceTopicRow.device_id == device_id)
        )
        await self._session.execute(
            delete(DeviceRow).where(DeviceRow.device_id == device_id)
        )
        await self._session.flush()

    async def list_devices_by_owner(self, owner_user_id: int) -> list[Device]:
        rows = (
            await self._session.scalars(
                select(DeviceRow).where(DeviceRow.owner_user_id == owner_user_id)
            )
        ).all()
        return [await self._to_device(r) for r in rows]

    async def list_devices_by_project(self, project_id: uuid.UUID) -> list[Device]:
        """Machines a project may run on = explicit per-project assignments UNION the
        project's TEAM's devices (execution-architecture v4: compute belongs to the
        team). A team device is one whose owner is an active member of the project's
        team — enroll a machine once for the team and every team project can use it.
        A personal project (single-member team) resolves to just that member's."""
        explicit = (
            await self._session.scalars(
                select(DeviceProjectRow.device_id).where(
                    DeviceProjectRow.project_id == project_id
                )
            )
        ).all()
        team_owned = (
            await self._session.scalars(
                select(DeviceRow.device_id)
                .join(
                    TeamUserRelation,
                    TeamUserRelation.user_id == DeviceRow.owner_user_id,
                )
                .join(Project, Project.team_id == TeamUserRelation.team_id)
                .where(
                    Project.id == project_id,
                    TeamUserRelation.deleted_at.is_(None),
                )
            )
        ).all()
        out: list[Device] = []
        for did in dict.fromkeys([*explicit, *team_owned]):  # de-dup, keep order
            device = await self.get_device(did)
            if device is not None:
                out.append(device)
        return out

    # -- assignments -------------------------------------------------------

    async def assign_project(self, device_id: str, project_id: uuid.UUID) -> None:
        if await self.is_assigned(device_id, project_id):
            return
        self._session.add(DeviceProjectRow(device_id=device_id, project_id=project_id))
        await self._session.flush()

    async def unassign_project(self, device_id: str, project_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(DeviceProjectRow).where(
                DeviceProjectRow.device_id == device_id,
                DeviceProjectRow.project_id == project_id,
            )
        )
        await self._session.flush()

    async def list_project_ids(self, device_id: str) -> list[uuid.UUID]:
        return list(
            (
                await self._session.scalars(
                    select(DeviceProjectRow.project_id).where(
                        DeviceProjectRow.device_id == device_id
                    )
                )
            ).all()
        )

    async def is_assigned(self, device_id: str, project_id: uuid.UUID) -> bool:
        row = await self._session.scalar(
            select(DeviceProjectRow.id).where(
                DeviceProjectRow.device_id == device_id,
                DeviceProjectRow.project_id == project_id,
            )
        )
        return row is not None

    # -- topic→device pin (affinity, v4) -----------------------------------

    async def topic_device(self, topic_id: uuid.UUID) -> str | None:
        return await self._session.scalar(
            select(DeviceTopicRow.device_id).where(DeviceTopicRow.topic_id == topic_id)
        )

    async def bind_topic_device(self, topic_id: uuid.UUID, device_id: str) -> None:
        # write-once: never overwrite an existing pin (affinity is permanent).
        if await self._session.get(DeviceTopicRow, topic_id) is not None:
            return
        self._session.add(DeviceTopicRow(topic_id=topic_id, device_id=device_id))
        await self._session.flush()
