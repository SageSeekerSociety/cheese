"""Project scripts: human stewardship, room pinning, and explicit application."""

from tests.conftest import seed_user


def project(client):
    headers = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}
    response = client.post(
        "/projects", json={"name": "Environment", "owner_handle": "alice"}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"], headers


def room(client, project_id):
    response = client.post(
        "/topics",
        json={"project_id": project_id, "title": "Room", "created_by": "alice"},
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
    for handle, role in (("bob", "member"), ("carol", "lead")):
        response = client.post(
            f"/projects/{project_id}/members",
            json={"user_handle": handle, "role": role},
            headers=owner,
        )
        assert response.status_code == 200, response.text
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
    import uuid

    from app.api.deps import get_chat_service

    project_id, headers = project(client)
    topic_id = room(client, project_id)
    base = f"/projects/{project_id}/environment"
    chat = client.app.dependency_overrides[get_chat_service]()
    chat._active_turn_ids[uuid.UUID(topic_id)] = uuid.uuid4()
    try:
        saved = client.put(base, headers=headers, json={"startup_script": "echo next"})
        assert saved.status_code == 200
        applied = client.post(
            f"{base}/rooms/{topic_id}/apply", headers=headers, json={"latest": True}
        )
        assert applied.status_code == 422, applied.text
        current = client.get(f"{base}/rooms/{topic_id}", headers=headers).json()["data"]
        assert current["pinned_revision"] != saved.json()["data"]["revision"]
    finally:
        chat._active_turn_ids.clear()


async def test_image_migration_keeps_other_settings_and_pins_all_rooms(db_factory):
    import importlib.util
    import uuid
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
