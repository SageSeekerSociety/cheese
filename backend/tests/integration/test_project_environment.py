"""Project scripts: human stewardship, room pinning, and explicit application."""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.agent.repositories import AgentTurnRepository
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)


def project(client):
    headers = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}
    response = post_project(client, json={"name": "Environment"}, owner="alice")
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"], headers


def room(client, project_id):
    response = client.post(
        "/topics",
        json={"project_id": project_id, "title": "Room"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_saving_pins_new_rooms_and_only_explicitly_changes_existing_rooms(client):
    project_id, headers = project(client)
    base = f"/projects/{project_id}/environment"
    first = room(client, project_id)
    original = client.get(base, headers=headers).json()["data"]["config"]["revision"]
    saved = client.put(base, headers=headers, json={"setup_script": "echo setup"})
    assert saved.status_code == 200, saved.text
    revision = saved.json()["data"]["revision"]
    second = room(client, project_id)
    data = client.get(base, headers=headers).json()["data"]
    revisions = {r["id"]: r["revision"] for r in data["rooms"]}
    assert revisions[first] == original
    assert revisions[second] == revision != original
    applied = client.post(
        f"{base}/rooms/{first}/apply", headers=headers, json={"latest": True}
    )
    assert applied.status_code == 200, applied.text
    state = client.get(f"{base}/rooms/{first}", headers=headers).json()["data"]
    assert state == {"state": "unbound", "pinned_revision": revision}


def test_a_room_that_has_not_picked_a_machine_says_so_instead_of_preparing(client):
    """没有设备绑定、也没有云机器在建的房间：不是在准备，是还没开始准备。

    说「正在准备」的那段时间里没有任何东西在动，而且不会自己变——挑机器发生在
    第一条消息进来的时候，所以房间会一直停在这个状态。"""
    project_id, headers = project(client)
    topic_id = room(client, project_id)

    state = client.get(
        f"/projects/{project_id}/environment/rooms/{topic_id}", headers=headers
    ).json()["data"]

    assert state["state"] == "unbound"


def test_only_human_stewards_can_change_scripts_and_members_can_read(client):
    project_id, owner = project(client)
    base = f"/projects/{project_id}/environment"
    join_project_team(client, project_id, "bob")
    join_project_team(client, project_id, "carol", admin=True)
    for handle, read, write in (
        ("bob", 200, 403),
        ("carol", 200, 200),
        ("eve", 403, 403),
    ):
        headers = {"Authorization": f"Bearer {seed_user(client, handle)}"}
        assert client.get(base, headers=headers).status_code == read
        assert client.put(base, headers=headers, json={}).status_code == write
    assert client.put(base, json={}).status_code == 401
    assert (
        client.put(base, headers=owner, json={"variables": {"HOME": "/"}}).status_code
        == 400
    )


def test_room_from_another_project_cannot_be_read_or_changed(client):
    project_id, headers = project(client)
    other_id, _ = project(client)
    other_room = room(client, other_id)
    path = f"/projects/{project_id}/environment/rooms/{other_room}"
    assert client.get(path, headers=headers).status_code == 404
    assert (
        client.post(f"{path}/apply", headers=headers, json={"latest": True}).status_code
        == 404
    )


def test_active_room_refuses_application_but_project_can_save_future_config(client):
    """「这一轮在跑」看的是库里的 open 行，不是这台进程的缓存。

    种进缓存不算数：那台跑着轮次的后端不是这一台，行才是跨进程的权威。所以这里种
    一行真开着的 ``AgentTurn``（作者、正文都由它自己的生命周期写），房间才真的在忙。
    """
    project_id, headers = project(client)
    topic_id = room(client, project_id)
    base = f"/projects/{project_id}/environment"

    async def seed_open_turn():
        async with client.test_request_factory() as session:
            work = uuid.uuid4()
            await AgentTurnRepository(session).open(
                turn_id=work,
                conversation_id=uuid.UUID(topic_id),
                continuation_id=work,
                author="cheese",
                content="在跑一轮活",
                is_resume=False,
                resendable=True,
                started_at=datetime.now(UTC),
                delivered_at=datetime.now(UTC),
                agent_handle="cheese",
            )
            await session.commit()

    client.portal.call(seed_open_turn)

    saved = client.put(base, headers=headers, json={"startup_script": "echo next"})
    assert saved.status_code == 200
    applied = client.post(
        f"{base}/rooms/{topic_id}/apply", headers=headers, json={"latest": True}
    )
    assert applied.status_code == 422, applied.text
    assert applied.json()["error"]["i18n"]["key"] == "roomBusyFinishBeforeEnvironment"
    current = client.get(f"{base}/rooms/{topic_id}", headers=headers).json()["data"]
    assert current["pinned_revision"] != saved.json()["data"]["revision"]


@pytest.mark.parametrize(
    ("place", "stopped", "busy"),
    [
        ("room", False, True),
        ("task", False, True),
        ("task", True, False),
        ("other", False, False),
    ],
)
def test_room_environment_busy_covers_its_tasks_but_not_stopped_or_other_work(
    client, place, stopped, busy
):
    from app.domain.agent.models import AgentTurn
    from app.domain.room_task.models import Task

    project_id, headers = project(client)
    topic_id = room(client, project_id)
    other_room = room(client, project_id) if place == "other" else None

    async def seed_turn():
        async with client.test_request_factory() as session:
            conversation_id = uuid.UUID(other_room or topic_id)
            if place == "task":
                task = Task(
                    project_id=uuid.UUID(project_id),
                    room_id=conversation_id,
                    title="Work using the room environment",
                )
                session.add(task)
                await session.flush()
                conversation_id = task.id
            work = uuid.uuid4()
            await AgentTurnRepository(session).open(
                turn_id=work,
                conversation_id=conversation_id,
                continuation_id=work,
                author="alice",
                content="Work",
                is_resume=False,
                resendable=True,
                started_at=datetime.now(UTC),
            )
            if stopped:
                turn = await session.get(AgentTurn, work)
                turn.stopped_at = datetime.now(UTC)
            await session.commit()

    client.portal.call(seed_turn)
    response = client.get(
        f"/projects/{project_id}/environment/rooms/{topic_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"].get("busy", False) is busy


async def test_image_migration_keeps_other_settings_and_pins_all_rooms(db_factory):
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.domain.project.environment import EnvironmentConfig

    spec = importlib.util.spec_from_file_location(
        "environment_migration",
        Path(__file__).parents[2]
        / "alembic/versions/d9a7e8c30142_project_environment_scripts.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def exercise(connection):
        connection.exec_driver_sql(
            "CREATE TEMP TABLE projects (id UUID, settings JSON)"
        )
        connection.exec_driver_sql("CREATE TEMP TABLE topics (project_id UUID)")
        projects = sa.table(
            "projects", sa.column("id", sa.Uuid()), sa.column("settings", sa.JSON())
        )
        topics = sa.table(
            "topics",
            sa.column("project_id", sa.Uuid()),
            sa.column("environment", sa.JSON()),
        )
        for image in (None, "cheesex-dev:v0", "custom:tag"):
            project_id = uuid.uuid4()
            settings = {"keep": "unchanged"}
            if image:
                settings["sandbox_image"] = image
            connection.execute(
                projects.insert().values(id=project_id, settings=settings)
            )
            connection.execute(topics.insert().values(project_id=project_id))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        rows = connection.execute(
            sa.select(projects.c.settings, topics.c.environment).join(
                topics, projects.c.id == topics.c.project_id
            )
        ).all()
        assert len(rows) == 3
        for settings, pinned in rows:
            assert settings["keep"] == "unchanged"
            assert "sandbox_image" not in settings
            assert pinned == settings["environment"]
            assert (
                EnvironmentConfig.model_validate(
                    {k: v for k, v in pinned.items() if k != "revision"}
                ).snapshot()
                == pinned
            )
        assert any(
            "pnpm install --frozen-lockfile" in p["startup_script"] for _, p in rows
        )
        assert any(
            "custom:tag" in p["setup_script"] and "exit 1" in p["setup_script"]
            for _, p in rows
        )

    async with db_factory() as session:
        connection = await session.connection()
        await connection.run_sync(exercise)
        await session.rollback()


def test_applying_holds_no_room_lock_while_the_machine_is_reset(client, monkeypatch):
    """While the machine is being told to stop the old environment, the room
    row stays free for other writers; a room archived during the reset keeps
    its old environment (dev outage of 2026-09-18: a row lock held across a
    device call queued every writer of the row with a pool connection each)."""
    import asyncio
    import threading
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from sqlalchemy import text

    from app.api.routes import project_environment as routes
    from app.domain.topic.models import Topic

    project_id, headers = project(client)
    topic_id = room(client, project_id)
    base = f"/projects/{project_id}/environment"
    before = client.get(f"{base}/rooms/{topic_id}", headers=headers).json()["data"]
    monkeypatch.setattr(
        routes,
        "leases_on_peoples_devices",
        AsyncMock(
            return_value=[{"device_id": "machine", "resource_id": str(uuid.uuid4())}]
        ),
    )
    monkeypatch.setattr(routes.device_hub, "is_online", lambda device_id: True)
    monkeypatch.setattr(routes.device_hub, "screens_for_topic", lambda tid: [])
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def slow_reset(*args, **kwargs):
        in_flight.set()
        await release.wait()
        return {"state": "pending"}

    monkeypatch.setattr(routes, "environment_status", slow_reset)
    outcome: dict = {}

    def apply():
        outcome["response"] = client.post(
            f"{base}/rooms/{topic_id}/apply", headers=headers, json={"latest": True}
        )

    request = threading.Thread(target=apply)
    request.start()

    async def during_reset():
        try:
            await asyncio.wait_for(in_flight.wait(), timeout=5)
            async with client.test_factory() as session:
                # NOWAIT fails at once if the request were holding the row.
                locked = await session.scalar(
                    text("SELECT id FROM topics WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": uuid.UUID(topic_id)},
                )
                assert locked == uuid.UUID(topic_id)
                topic = await session.get(Topic, uuid.UUID(topic_id))
                topic.archived_at = datetime.now(UTC)
                await session.commit()
        finally:
            release.set()  # a failed check must still let the request finish

    client.portal.call(during_reset)
    request.join(timeout=10)
    assert outcome["response"].status_code == 422, outcome["response"].text
    after = client.get(f"{base}/rooms/{topic_id}", headers=headers).json()["data"]
    assert after["pinned_revision"] == before["pinned_revision"]


def test_team_devices_form_one_sandbox_pool_regardless_of_enroller(client):
    from sqlalchemy import select

    from app.domain.device.supply import Supply
    from app.domain.device.wiring import sql_device_service
    from app.domain.machine import environments
    from app.domain.project.models import Project
    from app.domain.user.models import User

    pid, _ = project(client)
    seed_user(client, "bob")

    async def enroll_and_offer():
        async with client.test_factory() as db:
            row = await db.get(Project, uuid.UUID(pid))
            devices = sql_device_service(db)
            for handle in ["alice", "bob"]:
                user = await db.scalar(select(User.id).where(User.username == handle))
                await devices.approve(
                    await devices.start(handle),
                    owner_user_id=user,
                    team_id=row.team_id,
                    supply=Supply.self_hosted,
                )
            await db.flush()
            return await environments.offered_specs(db, row)

    specs = client.portal.call(enroll_and_offer)
    [pool] = [spec for spec in specs if spec["spec"] == "team_devices"]
    assert pool["label"] == "团队设备"


def test_each_platform_spec_says_the_hourly_price_it_is_charged_at(client, monkeypatch):
    """选规格的人看到的价格，就是那种算力实际按小时扣的额度：标准沙箱一个价，
    虚拟机按配置的创建规格（核数、内存）定价；没定价的规格照实说它起不来。"""
    from app.core.config import settings
    from app.domain.machine import environments
    from app.domain.project.models import Project

    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr(settings, "microcloud_vm_offering_id", 55)
    monkeypatch.setattr(settings, "cloud_vm_cores", 4)
    monkeypatch.setattr(settings, "cloud_vm_memory_mb", 8192)
    monkeypatch.setattr(settings, "cloud_sandbox_credits_per_hour", 7.5)
    monkeypatch.setattr(
        settings, "cloud_vm_credits_per_hour", {"2c4g": 9, "4c8g": 20.0}
    )
    pid, _ = project(client)

    async def offer():
        async with client.test_factory() as db:
            return await environments.offered_specs(
                db, await db.get(Project, uuid.UUID(pid))
            )

    about = {s["spec"]: s["description"] for s in client.portal.call(offer)}
    assert "每小时 7.5 点" in about["standard"]
    assert "4 核 8 GiB" in about["vm"] and "每小时 20 点" in about["vm"]

    monkeypatch.setattr(settings, "cloud_sandbox_credits_per_hour", None)
    monkeypatch.setattr(settings, "cloud_vm_credits_per_hour", {"2c4g": 9})
    about = {s["spec"]: s["description"] for s in client.portal.call(offer)}
    assert "点" not in about["standard"] and "无法启动" in about["standard"]
    assert "点" not in about["vm"] and "无法启动" in about["vm"]
