"""SQL-backed ``DeviceRepository`` (production). Converts rows ↔ dataclasses so the
service stays storage-agnostic. Same contract as ``InMemoryDeviceRepository``."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import (
    DeviceAuthCodeRow,
    DeviceHealthRow,
    DeviceRow,
    DeviceSandboxRow,
    HostedDeviceRow,
)
from app.domain.device.repository import (
    AuthCode,
    Device,
    HostHealth,
    SandboxCheck,
)
from app.domain.device.supply import Supply
from app.domain.project.models import Project
from app.domain.team import permissions as team_seats
from app.domain.team.permissions import Permission as TeamPermission


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

    async def delete_code(self, code: str) -> None:
        await self._session.execute(
            delete(DeviceAuthCodeRow).where(DeviceAuthCodeRow.code == code)
        )
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
        row.team_id = device.team_id
        row.owner_user_id = device.owner_user_id
        row.supply = device.supply
        row.last_seen_at = device.last_seen_at
        if device.supply is Supply.self_hosted:
            hosted = await self._session.get(HostedDeviceRow, device.device_id)
            if hosted is None:
                hosted = HostedDeviceRow(device_id=device.device_id)
                self._session.add(hosted)
            hosted.owner_user_id = device.owner_user_id
        await self._session.flush()

    async def note_last_seen(self, device_id: str, at: datetime) -> None:
        # This one column, not the row: it is written while a machine is
        # connected, which during a deploy may be while a migration has
        # dropped another.
        await self._session.execute(
            update(DeviceRow)
            .where(DeviceRow.device_id == device_id)
            .values(last_seen_at=at)
        )

    @staticmethod
    def _device(row: DeviceRow) -> Device:
        return Device(
            device_id=row.device_id,
            name=row.name,
            owner_user_id=row.owner_user_id,
            team_id=row.team_id,
            created_at=_aware(row.created_at),
            supply=row.supply,
            last_seen_at=_aware(row.last_seen_at) if row.last_seen_at else None,
        )

    async def get_device(self, device_id: str) -> Device | None:
        row = await self._session.get(DeviceRow, device_id)
        return self._device(row) if row is not None else None

    async def get_devices(self, device_ids: Sequence[str]) -> dict[str, Device]:
        if not device_ids:
            return {}
        rows = await self._session.scalars(
            select(DeviceRow).where(DeviceRow.device_id.in_(device_ids))
        )
        return {row.device_id: self._device(row) for row in rows}

    async def get_hosted_device(self, device_id: str) -> Device | None:
        row = await self._session.scalar(
            select(DeviceRow)
            .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
            .where(DeviceRow.device_id == device_id)
        )
        return self._device(row) if row is not None else None

    async def get_device_by_token_digest(self, digest: str) -> Device | None:
        row = await self._session.scalar(
            select(DeviceRow).where(DeviceRow.token_digest == digest)
        )
        return self._device(row) if row is not None else None

    async def set_token_digest(self, device_id: str, digest: str) -> bool:
        # One statement, so two polls racing for the same device cannot both
        # be handed a token.
        done = await self._session.execute(
            update(DeviceRow)
            .where(DeviceRow.device_id == device_id, DeviceRow.token_digest.is_(None))
            .values(token_digest=digest)
            .returning(DeviceRow.device_id)
        )
        return done.first() is not None

    async def replace_token_digest(self, device_id: str, digest: str) -> None:
        await self._session.execute(
            update(DeviceRow)
            .where(DeviceRow.device_id == device_id)
            .values(token_digest=digest)
        )

    async def delete_device(self, device_id: str) -> None:
        await self._session.execute(
            delete(DeviceRow).where(DeviceRow.device_id == device_id)
        )
        await self._session.flush()

    async def list_enrolled_by(self, owner_user_id: int) -> list[Device]:
        rows = (
            await self._session.scalars(
                select(DeviceRow)
                .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
                .where(HostedDeviceRow.owner_user_id == owner_user_id)
            )
        ).all()
        return [self._device(r) for r in rows]

    async def device_ids_by_project(self, project_id: uuid.UUID) -> list[str]:
        return list(
            await self._session.scalars(
                select(DeviceRow.device_id)
                .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
                .join(Project, Project.team_id == DeviceRow.team_id)
                .where(Project.id == project_id)
            )
        )

    async def serves_project(self, device_id: str, project_id: uuid.UUID) -> bool:
        return (
            await self._session.scalar(
                select(DeviceRow.device_id)
                .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
                .join(Project, Project.team_id == DeviceRow.team_id)
                .where(Project.id == project_id, DeviceRow.device_id == device_id)
            )
            is not None
        )

    async def list_devices_by_project(self, project_id: uuid.UUID) -> list[Device]:
        rows = await self._session.scalars(
            select(DeviceRow)
            .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
            .join(Project, Project.team_id == DeviceRow.team_id)
            .where(Project.id == project_id)
        )
        return [self._device(row) for row in rows]

    async def list_devices_by_team(self, team_id: int) -> list[Device]:
        rows = await self._session.scalars(
            select(DeviceRow)
            .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
            .where(DeviceRow.team_id == team_id)
        )
        return [self._device(row) for row in rows]

    async def may_manage_machines(self, team_id: int, user_id: int) -> bool:
        return await team_seats.may(
            self._session, team_id, user_id, TeamPermission.manage_machines
        )

    # -- machine health (#186) ---------------------------------------------

    @staticmethod
    def _health(row: DeviceHealthRow) -> HostHealth:
        return HostHealth(
            device_id=row.device_id,
            consecutive_failures=row.consecutive_failures,
            last_failure_code=row.last_failure_code,
            last_failure_at=(
                _aware(row.last_failure_at) if row.last_failure_at else None
            ),
            quarantined_until=(
                _aware(row.quarantined_until) if row.quarantined_until else None
            ),
        )

    async def get_host_health(self, device_id: str) -> HostHealth | None:
        row = await self._session.get(DeviceHealthRow, device_id)
        return self._health(row) if row is not None else None

    async def save_host_health(self, health: HostHealth) -> None:
        row = await self._session.get(DeviceHealthRow, health.device_id)
        if row is None:
            row = DeviceHealthRow(device_id=health.device_id)
            self._session.add(row)
        row.consecutive_failures = health.consecutive_failures
        row.last_failure_code = health.last_failure_code
        row.last_failure_at = health.last_failure_at
        row.quarantined_until = health.quarantined_until
        await self._session.flush()

    async def clear_host_health(self, device_id: str) -> None:
        await self._session.execute(
            delete(DeviceHealthRow).where(DeviceHealthRow.device_id == device_id)
        )
        await self._session.flush()

    async def list_host_health(
        self, device_ids: Sequence[str]
    ) -> dict[str, HostHealth]:
        if not device_ids:
            return {}
        rows = (
            await self._session.scalars(
                select(DeviceHealthRow).where(
                    DeviceHealthRow.device_id.in_(list(device_ids))
                )
            )
        ).all()
        return {row.device_id: self._health(row) for row in rows}

    async def save_sandbox_check(self, check: SandboxCheck) -> None:
        row = await self._session.get(DeviceSandboxRow, check.device_id)
        if row is None:
            row = DeviceSandboxRow(device_id=check.device_id)
            self._session.add(row)
        row.isolates = check.isolates
        row.reason = check.reason
        row.checked_at = check.checked_at
        await self._session.flush()

    async def list_sandbox_checks(
        self, device_ids: Sequence[str]
    ) -> dict[str, SandboxCheck]:
        if not device_ids:
            return {}
        rows = await self._session.scalars(
            select(DeviceSandboxRow).where(
                DeviceSandboxRow.device_id.in_(list(device_ids))
            )
        )
        return {
            row.device_id: SandboxCheck(
                row.device_id, row.isolates, row.reason, row.checked_at
            )
            for row in rows
        }
