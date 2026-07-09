"""PostgreSQL-backed ``DeviceRepository`` (Act 3).

Implements the same Protocol as ``InMemoryDeviceRepository`` so ``DeviceService`` is
unchanged — the connector plane swaps this in at one line. Because the connector's
consumers are long-lived (the ``/agent`` WebSocket) rather than request-scoped, this
repository takes a **session factory** and opens a short session per operation,
rather than holding one shared session.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import DeviceAuthCodeRow, DeviceProjectRow, DeviceRow
from .repository import AuthCode, Device


def _to_code(row: DeviceAuthCodeRow | None) -> AuthCode | None:
    if row is None:
        return None
    return AuthCode(
        code=row.code,
        device_name=row.device_name,
        status=row.status,
        created_at=row.created_at,
        device_id=row.device_id,
    )


def _to_device(row: DeviceRow | None) -> Device | None:
    if row is None:
        return None
    return Device(
        device_id=row.device_id,
        name=row.name,
        token=row.token,
        owner_user_id=row.owner_user_id,
        created_at=row.created_at,
    )


async def _assigned(session: AsyncSession, device_id: str, project_id: int) -> bool:
    row = (
        await session.execute(
            select(DeviceProjectRow.id).where(
                DeviceProjectRow.device_id == device_id,
                DeviceProjectRow.project_id == project_id,
            )
        )
    ).first()
    return row is not None


class SqlDeviceRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def save_code(self, code: AuthCode) -> None:
        async with self._sf() as session:
            row = await session.get(DeviceAuthCodeRow, code.code)
            if row is None:
                session.add(
                    DeviceAuthCodeRow(
                        code=code.code,
                        device_name=code.device_name,
                        status=code.status,
                        device_id=code.device_id,
                        created_at=code.created_at,
                    )
                )
            else:
                row.device_name = code.device_name
                row.status = code.status
                row.device_id = code.device_id
            await session.commit()

    async def get_code(self, code: str) -> AuthCode | None:
        async with self._sf() as session:
            return _to_code(await session.get(DeviceAuthCodeRow, code))

    async def save_device(self, device: Device) -> None:
        async with self._sf() as session:
            row = await session.get(DeviceRow, device.device_id)
            if row is None:
                session.add(
                    DeviceRow(
                        device_id=device.device_id,
                        name=device.name,
                        token=device.token,
                        owner_user_id=device.owner_user_id,
                        created_at=device.created_at,
                    )
                )
            else:
                row.name = device.name
                row.token = device.token
                row.owner_user_id = device.owner_user_id
            await session.commit()

    async def get_device(self, device_id: str) -> Device | None:
        async with self._sf() as session:
            return _to_device(await session.get(DeviceRow, device_id))

    async def get_device_by_token(self, token: str) -> Device | None:
        async with self._sf() as session:
            row = (
                await session.execute(select(DeviceRow).where(DeviceRow.token == token))
            ).scalar_one_or_none()
            return _to_device(row)

    async def list_devices_by_owner(self, owner_user_id: int) -> list[Device]:
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(DeviceRow)
                    .where(DeviceRow.owner_user_id == owner_user_id)
                    .order_by(DeviceRow.created_at)
                )
            ).scalars()
            return [d for d in (_to_device(r) for r in rows) if d is not None]

    async def delete_device(self, device_id: str) -> None:
        async with self._sf() as session:
            await session.execute(
                delete(DeviceProjectRow).where(DeviceProjectRow.device_id == device_id)
            )
            await session.execute(delete(DeviceRow).where(DeviceRow.device_id == device_id))
            await session.commit()

    async def assign_project(self, device_id: str, project_id: int) -> None:
        async with self._sf() as session:
            if not await _assigned(session, device_id, project_id):
                session.add(
                    DeviceProjectRow(
                        device_id=device_id, project_id=project_id, created_at=datetime.now(UTC)
                    )
                )
                await session.commit()

    async def unassign_project(self, device_id: str, project_id: int) -> None:
        async with self._sf() as session:
            await session.execute(
                delete(DeviceProjectRow).where(
                    DeviceProjectRow.device_id == device_id,
                    DeviceProjectRow.project_id == project_id,
                )
            )
            await session.commit()

    async def list_project_ids(self, device_id: str) -> list[int]:
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(DeviceProjectRow.project_id)
                    .where(DeviceProjectRow.device_id == device_id)
                    .order_by(DeviceProjectRow.project_id)
                )
            ).scalars()
            return list(rows)

    async def is_assigned(self, device_id: str, project_id: int) -> bool:
        async with self._sf() as session:
            return await _assigned(session, device_id, project_id)

    async def list_devices_by_project(self, project_id: int) -> list[Device]:
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(DeviceRow)
                    .join(DeviceProjectRow, DeviceProjectRow.device_id == DeviceRow.device_id)
                    .where(DeviceProjectRow.project_id == project_id)
                    .order_by(DeviceRow.created_at)
                )
            ).scalars()
            return [d for d in (_to_device(r) for r in rows) if d is not None]
