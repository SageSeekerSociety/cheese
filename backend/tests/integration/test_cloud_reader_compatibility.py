"""Reader-first release tolerates later session allocations without creating them."""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.domain.machine.models import AiStatus, MachineStatus, ProjectMachine
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import MachineService
from app.domain.topic import retire
from app.domain.topic.models import RoomCleanup
from tests.integration.test_cloud_warm_pool import warm_case as warm_case


def test_legacy_readers_keep_their_lease_and_cleanup_accounts_for_session_rows(
    warm_case, monkeypatch
):
    client, topics, actor, cloud = warm_case
    monkeypatch.setattr(retire.ws, "topic_worktrees_on_disk", lambda: [])

    async def run():
        async with client.test_factory() as db:
            service = MachineService(db, cloud)
            topic = uuid.UUID(topics[0])
            legacy = await service.ensure_topic_machine(topic, actor=actor)
            future = []
            for number in range(2):
                # Data shape written by the later feature, not a writer enabled
                # by this compatibility release. Distinct owners in one room.
                row = ProjectMachine(
                    project_id=legacy.project_id,
                    topic_id=topic,
                    session_id=uuid.uuid4(),
                    machine_id=300 + number,
                    device_id=f"future-{number}",
                    status=MachineStatus.running,
                    ai_status=AiStatus.ready,
                    **{
                        key: getattr(legacy, key)
                        for key in (
                            "customer_id",
                            "account_id",
                            "offering_id",
                            "hostname",
                            "login_user",
                            "cores",
                            "memory_mb",
                            "disk_gb",
                        )
                    },
                )
                db.add(row)
                future.append(row)
            await db.commit()
            repo = ProjectMachineRepository(db)
            assert (await repo.get_active_for_topic(topic)).id == legacy.id
            assert (
                await service.ensure_topic_machine(topic, actor=actor)
            ).id == legacy.id
            assert await service.ready_topic_devices() == [(topic, "warm-test")]
            for row in future:
                row.status = MachineStatus.error
            await db.flush()
            assert await service.failed_topic_leases() == []
            all_ids = {legacy.id, *(row.id for row in future)}
            assert {
                row.id for row in await service.list_active_for_topic(topic)
            } == all_ids

            # Account for every VM even before its connector has enrolled.
            # This invokes the actual cleanup inventory, with no storage I/O.
            for row in [legacy, *future]:
                row.device_id = None
            await db.flush()
            operation = RoomCleanup(
                id=uuid.uuid4(),
                topic_id=topic,
                project_id=legacy.project_id,
                resource_id=topic,
            )
            entries = await retire._inventory(db, operation, {})
            assert {
                uuid.UUID(entry["id"])
                for entry in entries
                if entry["kind"] == "machine"
            } == all_ids
            await service.detach_archived_machine(topic)
            await db.commit()
            assert not await service.list_active_for_topic(topic)
            assert cloud.deleted == []
            assert len(cloud.claims) == 1 and cloud.created == []

    asyncio.run(run())


@pytest.mark.anyio
async def test_cloud_ownership_migration_preserves_legacy_allocation(db_factory):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location(
        "cloud_ownership_migration",
        Path(__file__).parents[2]
        / "alembic/versions/b672a09ef831_cloud_session_ownership.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def exercise(connection):
        connection.exec_driver_sql(
            "CREATE TEMP TABLE project_machines "
            "(id UUID PRIMARY KEY, topic_id UUID, released_at TIMESTAMPTZ)"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_project_machines_active_topic "
            "ON project_machines(topic_id) WHERE released_at IS NULL"
        )
        room, legacy = uuid.uuid4(), uuid.uuid4()
        connection.execute(
            text("INSERT INTO project_machines(id,topic_id) VALUES (:id,:topic)"),
            {"id": legacy, "topic": room},
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        row = connection.exec_driver_sql(
            "SELECT id,session_id FROM project_machines"
        ).one()
        assert row == (legacy, None)
        for _ in range(2):
            connection.execute(
                text(
                    "INSERT INTO project_machines(id,topic_id,session_id) "
                    "VALUES (:id,:topic,:session)"
                ),
                {"id": uuid.uuid4(), "topic": room, "session": uuid.uuid4()},
            )
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM project_machines").scalar()
            == 3
        )
        with pytest.raises(RuntimeError, match="Release session"):
            migration.downgrade()

    async with db_factory() as db:
        await (await db.connection()).run_sync(exercise)
        await db.rollback()
