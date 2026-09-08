"""SQL-backed ``DeviceRepository`` (production). Converts rows ↔ dataclasses so the
service stays storage-agnostic. Same contract as ``InMemoryDeviceRepository``."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import (
    DeviceAuthCodeRow,
    DeviceHealthRow,
    DeviceProjectRow,
    DeviceRow,
    DeviceTeamRow,
    DeviceTopicRow,
    HostedDeviceRow,
)
from app.domain.device.repository import AuthCode, Device, HostHealth, TopicDevice
from app.domain.device.supply import Supply, Visibility
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.user.models import User


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
        row.supply = device.supply
        row.visibility = device.visibility
        if device.supply is Supply.self_hosted:
            hosted = await self._session.get(HostedDeviceRow, device.device_id)
            if hosted is None:
                hosted = HostedDeviceRow(device_id=device.device_id)
                self._session.add(hosted)
            hosted.owner_user_id = device.owner_user_id
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
        team_ids = list(
            (
                await self._session.scalars(
                    select(DeviceTeamRow.team_id).where(
                        DeviceTeamRow.device_id == row.device_id
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
            team_ids=team_ids,
            supply=row.supply,
            visibility=row.visibility,
            ccproxy_upstream=row.ccproxy_upstream,
            ccproxy_machine_id=row.ccproxy_machine_id,
        )

    async def get_device(self, device_id: str) -> Device | None:
        row = await self._session.get(DeviceRow, device_id)
        return await self._to_device(row) if row is not None else None

    async def get_hosted_device(self, device_id: str) -> Device | None:
        row = await self._session.scalar(
            select(DeviceRow)
            .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
            .where(DeviceRow.device_id == device_id)
        )
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
            delete(DeviceTeamRow).where(DeviceTeamRow.device_id == device_id)
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
                select(DeviceRow)
                .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
                .where(HostedDeviceRow.owner_user_id == owner_user_id)
            )
        ).all()
        return [await self._to_device(r) for r in rows]

    async def _device_ids_by_project(self, project_id: uuid.UUID) -> list[str]:
        """Machines a project may run on = explicit per-project assignments UNION the
        devices bound to the project's TEAM (execution-architecture v4: compute
        belongs to the team — 为团队注册设备). Bind a machine to a team once and every
        project of that team can run on it. A project with NO team is a personal
        project: it resolves through its owner's PERSONAL team (个人 = 单人真团队),
        so 为自己注册的设备 reach personal projects with zero per-project setup."""
        explicit = (
            await self._session.scalars(
                select(DeviceProjectRow.device_id).where(
                    DeviceProjectRow.project_id == project_id
                )
            )
        ).all()
        team_bound = (
            await self._session.scalars(
                select(DeviceTeamRow.device_id)
                .join(Project, Project.team_id == DeviceTeamRow.team_id)
                .where(Project.id == project_id)
            )
        ).all()
        # Personal-project route: owner_handle == User.username → that user's
        # personal team. Resolved at read time so it needs no backfill and keeps
        # working for projects created before personal teams existed.
        personal_bound = (
            await self._session.scalars(
                select(DeviceTeamRow.device_id)
                .join(Team, Team.id == DeviceTeamRow.team_id)
                .join(User, User.id == Team.personal_owner_user_id)
                .join(Project, Project.owner_handle == User.username)
                .where(
                    Project.id == project_id,
                    Project.team_id.is_(None),
                    Team.deleted_at.is_(None),
                )
            )
        ).all()
        return list(dict.fromkeys([*explicit, *team_bound, *personal_bound]))

    async def list_devices_by_project(self, project_id: uuid.UUID) -> list[Device]:
        """Human-hosted machines assigned directly or through the project's team."""
        out: list[Device] = []
        for did in await self._device_ids_by_project(project_id):
            device = await self.get_hosted_device(did)
            if device is not None:
                out.append(device)
        return out

    async def list_devices_by_team(self, team_id: int) -> list[Device]:
        device_ids = (
            await self._session.scalars(
                select(DeviceTeamRow.device_id).where(DeviceTeamRow.team_id == team_id)
            )
        ).all()
        out: list[Device] = []
        for did in device_ids:
            device = await self.get_hosted_device(did)
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

    # -- device↔team bindings (为团队注册设备, v4) --------------------------

    async def assign_team(self, device_id: str, team_id: int) -> None:
        existing = await self._session.scalar(
            select(DeviceTeamRow.id).where(
                DeviceTeamRow.device_id == device_id,
                DeviceTeamRow.team_id == team_id,
            )
        )
        if existing is not None:
            return  # idempotent — keep the existing bind
        self._session.add(DeviceTeamRow(device_id=device_id, team_id=team_id))
        await self._session.flush()

    async def unassign_team(self, device_id: str, team_id: int) -> None:
        await self._session.execute(
            delete(DeviceTeamRow).where(
                DeviceTeamRow.device_id == device_id,
                DeviceTeamRow.team_id == team_id,
            )
        )
        await self._session.flush()

    async def list_team_ids(self, device_id: str) -> list[int]:
        return list(
            (
                await self._session.scalars(
                    select(DeviceTeamRow.team_id).where(
                        DeviceTeamRow.device_id == device_id
                    )
                )
            ).all()
        )

    # -- topic→device pin (affinity, v4) -----------------------------------

    async def topic_binding(self, topic_id: uuid.UUID) -> TopicDevice | None:
        row = await self._session.get(DeviceTopicRow, topic_id)
        if row is None:
            return None
        return TopicDevice(
            topic_id=row.topic_id,
            device_id=row.device_id,
            visibility=row.visibility,
        )

    async def list_topic_bindings(self, device_id: str) -> list[TopicDevice]:
        rows = (
            await self._session.scalars(
                select(DeviceTopicRow).where(DeviceTopicRow.device_id == device_id)
            )
        ).all()
        return [
            TopicDevice(
                topic_id=row.topic_id,
                device_id=row.device_id,
                visibility=row.visibility,
            )
            for row in rows
        ]

    async def bind_topic_device(
        self, topic_id: uuid.UUID, device_id: str, visibility: Visibility
    ) -> None:
        # write-once: never overwrite an existing pin (affinity never drifts).
        if await self._session.get(DeviceTopicRow, topic_id) is not None:
            return
        self._session.add(
            DeviceTopicRow(
                topic_id=topic_id, device_id=device_id, visibility=visibility
            )
        )
        await self._session.flush()

    async def release_topic_device(self, topic_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(DeviceTopicRow).where(DeviceTopicRow.topic_id == topic_id)
        )
        await self._session.flush()

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
