"""The migration binds rooms that already ran on an enrolled machine `host`.

A room on automatic selection was never bound to the enrolled machine its
sessions took, and saw the whole machine as every room did then. Now an
unbound room runs isolated, so without a binding it would lose what it had.
"""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select

from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import post_project, session_auth_headers

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIGRATION = "*_rooms_that_ran_on_own_machines_keep_them.py"


def _upgrade(client) -> None:
    path = next(_VERSIONS.glob(_MIGRATION))
    spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

    async def go() -> None:
        async with client.test_factory() as db:
            await (await db.connection()).run_sync(apply)
            await db.commit()

    asyncio.run(go())


def test_rooms_keep_the_enrolled_machine_they_ran_on(client):
    pid = post_project(client, json={"name": "P"}, owner="keeper").json()["data"]["id"]

    def room() -> uuid.UUID:
        made = client.post(
            "/topics",
            json={"project_id": pid, "title": "T"},
            headers=session_auth_headers("keeper"),
        )
        return uuid.UUID(made.json()["data"]["id"])

    automatic, moved, now_cloud, chose_cloud, named, never = (room() for _ in range(6))
    start = datetime(2026, 9, 1, tzinfo=UTC)

    async def seed() -> dict[str, str]:
        async with client.test_factory() as db:
            owner = await db.scalar(select(User).where(User.username == "keeper"))
            devices = sql_device_service(db)
            ids = {}
            for name, supply in (
                ("first", Supply.self_hosted),
                ("second", Supply.self_hosted),
                ("cloud", Supply.cloud),
            ):
                device = await devices.approve(
                    await devices.start(name), owner_user_id=owner.id, supply=supply
                )
                ids[name] = device.device_id
            sessions = AgentSessionService(db)

            async def ran(topic, handle, device, minutes):
                row = await sessions.ensure(topic, handle, harness="claude-code")
                row.work_lease = {"kind": "device", "device_id": ids[device]}
                row.placed_at = start + timedelta(minutes=minutes)

            await ran(automatic, "analyst", "first", 0)
            # Used both enrolled machines; works on the second now.
            await ran(moved, "analyst", "first", 0)
            await ran(moved, "builder", "second", 5)
            # Ran on an enrolled machine, then moved to Cloud.
            await ran(now_cloud, "analyst", "first", 0)
            await ran(now_cloud, "builder", "cloud", 5)
            await ran(chose_cloud, "analyst", "first", 0)
            (await db.get(Topic, chose_cloud)).compute_config = {
                "profile": "cloud",
                "name": None,
                "device_id": None,
            }
            # Named a machine and was bound isolated: it keeps that.
            await ran(named, "analyst", "first", 0)
            await devices.bind_topic_device(named, ids["first"], Visibility.isolated)
            await db.commit()
            return ids

    ids = asyncio.run(seed())

    _upgrade(client)

    async def bindings():
        async with client.test_factory() as db:
            devices = sql_device_service(db)
            out = {}
            for topic in (automatic, moved, now_cloud, chose_cloud, named, never):
                binding = await devices.topic_binding(topic)
                out[topic] = (
                    (binding.device_id, binding.visibility) if binding else None
                )
            return out

    bound = asyncio.run(bindings())
    assert bound[automatic] == (ids["first"], Visibility.host)
    assert bound[moved] == (ids["second"], Visibility.host)
    assert bound[now_cloud] is None
    assert bound[chose_cloud] is None
    assert bound[named] == (ids["first"], Visibility.isolated)
    assert bound[never] is None

    # Running it again changes nothing.
    _upgrade(client)
    assert asyncio.run(bindings()) == bound
