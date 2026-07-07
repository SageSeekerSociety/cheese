"""DB-backed test for the PostgreSQL ``SqlDeviceRepository`` (Act 3).

Runs the full device flow through ``DeviceService`` on the real ``device`` /
``device_auth_code`` tables (requires the migrated dev DB). Self-contained: its own
engine bound to the test loop, unique identifiers, and row cleanup — it does not use
the SAVEPOINT app fixtures (the repository opens its own committing sessions).
"""

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import async_url
from app.domain.device import DeviceService
from app.domain.device.models import DeviceAuthCodeRow, DeviceProjectRow, DeviceRow
from app.domain.device.sql_repository import SqlDeviceRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_full_device_flow_on_postgres() -> None:
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = DeviceService(SqlDeviceRepository(factory))
    name = f"pg-test-{uuid.uuid4().hex[:8]}"
    created_device_id: str | None = None
    code: str | None = None
    try:
        code = await svc.start(name)

        # Pending before approval — no token leaks.
        assert await svc.poll(code) == {"status": "pending"}

        device = await svc.approve(code, actor_user_id=4242)
        created_device_id = device.device_id
        assert device.owner_user_id == 4242
        assert device.token

        # Poll now returns the durable token — persisted and read back from PG.
        approved = await svc.poll(code)
        assert approved["status"] == "approved"
        assert approved["token"] == device.token
        assert approved["device_id"] == device.device_id

        # Token verification + owner resolution round-trip through the DB.
        fetched = await svc.verify_token(device.token)
        assert fetched is not None and fetched.device_id == device.device_id
        assert await svc.resolve_owner(device.token) == 4242
        assert (await svc.get_device(device.device_id)) is not None

        # Rename persists.
        renamed = await svc.rename(device.token, "pg-renamed")
        assert renamed.name == "pg-renamed"
        again = await svc.get_device(device.device_id)
        assert again is not None and again.name == "pg-renamed"

        # Idempotent approve: same device, no second token.
        device2 = await svc.approve(code, actor_user_id=4242)
        assert device2.device_id == device.device_id
        assert device2.token == device.token

        # Device↔project assignment persists in PG.
        assert await svc.list_projects(device.device_id) == []
        await svc.assign_to_project(device.device_id, 555, actor_user_id=4242)
        await svc.assign_to_project(device.device_id, 555, actor_user_id=4242)  # idempotent
        assert await svc.serves_project(device.device_id, 555) is True
        assert await svc.list_projects(device.device_id) == [555]
        await svc.unassign_from_project(device.device_id, 555, actor_user_id=4242)
        assert await svc.list_projects(device.device_id) == []
    finally:
        async with factory() as session:
            if created_device_id is not None:
                await session.execute(
                    delete(DeviceProjectRow).where(DeviceProjectRow.device_id == created_device_id)
                )
                await session.execute(delete(DeviceRow).where(DeviceRow.device_id == created_device_id))
            if code is not None:
                await session.execute(delete(DeviceAuthCodeRow).where(DeviceAuthCodeRow.code == code))
            await session.commit()
        await engine.dispose()
