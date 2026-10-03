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

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_cloud_sessions_are_isolated.py"
    )
)


def _load():
    spec = importlib.util.spec_from_file_location("_mig_cloud_isolated", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cloud_bindings_become_isolated_and_self_hosted_ones_stay(client):
    seed_user(client, "isolated_owner")
    cloud_topic, own_topic = uuid.uuid4(), uuid.uuid4()

    async def seed() -> tuple[str, str]:
        async with client.test_factory() as db:
            devices = sql_device_service(db)
            user = await db.scalar(
                select(User).where(User.username == "isolated_owner")
            )
            assert user is not None
            ids = []
            for name, supply in (("cloud", Supply.cloud), ("own", Supply.self_hosted)):
                device = await devices.approve(
                    await devices.start(name),
                    owner_user_id=user.id,
                    supply=supply,
                    visibility=Visibility.host,
                )
                ids.append(device.device_id)
            await devices.bind_topic_device(cloud_topic, ids[0], Visibility.host)
            await devices.bind_topic_device(own_topic, ids[1], Visibility.host)
            await db.commit()
            return ids[0], ids[1]

    cloud, own = asyncio.run(seed())

    def run(step: str):
        def apply(conn) -> None:
            with Operations.context(MigrationContext.configure(conn)):
                getattr(_load(), step)()

        async def go():
            async with client.test_factory() as db:
                await (await db.connection()).run_sync(apply)
                await db.commit()
            async with client.test_factory() as db:
                devices = sql_device_service(db)
                return (
                    (await devices.topic_binding(cloud_topic)).visibility,
                    (await devices.topic_binding(own_topic)).visibility,
                    (await devices.get_device(cloud)).visibility,
                    (await devices.get_device(own)).visibility,
                )

        return asyncio.run(go())

    assert run("upgrade") == (
        Visibility.isolated,
        Visibility.host,
        Visibility.isolated,
        Visibility.host,
    )
    assert run("downgrade") == (Visibility.host,) * 4
