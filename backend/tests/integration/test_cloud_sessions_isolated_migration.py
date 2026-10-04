"""The migration makes what is already bound on Cloud machines `isolated`.

Each session on a Cloud machine runs in a sandbox of its own now (#2320), which
is what a Cloud machine's bindings say from here on. The ones written before
said `host`; a self-hosted machine's keep what they say.
"""

import asyncio
import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select

from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.user.models import User
from tests.conftest import seed_user

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load(pattern: str):
    path = next(_VERSIONS.glob(pattern))
    spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ISOLATED = "*_cloud_sessions_are_isolated.py"
# The next revision drops the device column this migration also writes; stepping
# back over it puts the schema where this migration runs.
_NEXT = "*_drop_device_visibility.py"


def test_cloud_bindings_become_isolated_and_self_hosted_ones_stay(client):
    seed_user(client, "isolated_owner")
    cloud_topic, own_topic = uuid.uuid4(), uuid.uuid4()

    async def seed() -> None:
        async with client.test_factory() as db:
            devices = sql_device_service(db)
            user = await db.scalar(
                select(User).where(User.username == "isolated_owner")
            )
            assert user is not None
            ids = []
            for name, supply in (("cloud", Supply.cloud), ("own", Supply.self_hosted)):
                device = await devices.approve(
                    await devices.start(name), owner_user_id=user.id, supply=supply
                )
                ids.append(device.device_id)
            await devices.bind_topic_device(cloud_topic, ids[0], Visibility.host)
            await devices.bind_topic_device(own_topic, ids[1], Visibility.host)
            await db.commit()

    asyncio.run(seed())

    def run(*steps: tuple[str, str]):
        def apply(conn) -> None:
            with Operations.context(MigrationContext.configure(conn)):
                for pattern, step in steps:
                    getattr(_load(pattern), step)()

        async def go():
            async with client.test_factory() as db:
                await (await db.connection()).run_sync(apply)
                await db.commit()
            async with client.test_factory() as db:
                devices = sql_device_service(db)
                return (
                    (await devices.topic_binding(cloud_topic)).visibility,
                    (await devices.topic_binding(own_topic)).visibility,
                )

        return asyncio.run(go())

    upgraded = run((_NEXT, "downgrade"), (_ISOLATED, "upgrade"))
    try:
        assert upgraded == (Visibility.isolated, Visibility.host)
        assert run((_ISOLATED, "downgrade")) == (Visibility.host, Visibility.host)
    finally:
        run((_NEXT, "upgrade"))
