"""The second ownership release preserves enrollments and saved environments."""

import json
import uuid

from tests.integration.migration_replay import database_at, seed_room, seed_task

OWNERSHIP = "c9e25a7b38f1"
CLEANUP = "d1f37c8a46b2"


def test_cleanup_keeps_devices_and_sandboxes_and_moves_saved_choices_to_the_team():
    with database_at(OWNERSHIP) as db:
        project, room = seed_room(db)
        task = seed_task(db, project, room)
        team = db.fetchval("SELECT team_id FROM projects WHERE id = $1", project)
        owner = db.fetchval(
            'INSERT INTO "user" (username, email, created_at, updated_at)'
            " VALUES ('enroller', 'enroller@example.test', now(), now()) RETURNING id"
        )
        db.execute(
            "INSERT INTO device (device_id, name, token, owner_user_id, team_id, created_at)"
            " VALUES ('enrolled', 'Workstation', 'device-token', $1, $2, now())",
            owner,
            team,
        )
        db.execute(
            "INSERT INTO hosted_device (device_id, owner_user_id) VALUES ('enrolled', $1)",
            owner,
        )
        for table, target, value in (
            ("device_team", "team_id", team),
            ("device_project", "project_id", project),
        ):
            db.execute(
                f"INSERT INTO {table} (id, device_id, {target}, created_at, updated_at)"
                " VALUES ($1, 'enrolled', $2, now(), now())",
                uuid.uuid4(),
                value,
            )
        legacy = {"kind": "sandbox", "spec": f"devices_of:{owner}"}
        cloud = {"kind": "sandbox", "spec": "standard"}
        machine = {"kind": "self_hosted", "device_id": "enrolled"}
        for handle, environment in (
            ("local", legacy),
            ("cloud", cloud),
            ("machine", machine),
        ):
            db.execute(
                "INSERT INTO agent_environments"
                " (conversation_id, agent_handle, environment, chosen_by, updated_at)"
                ' VALUES ($1, $2, $3::jsonb, \'{"kind":"user","user_id":1}\', now())',
                room,
                handle,
                json.dumps(environment),
            )
        for table, item in (("topics", room), ("tasks", task)):
            db.execute(
                f"UPDATE {table} SET default_environment = $1 WHERE id = $2",
                json.dumps(legacy),
                item,
            )
        db.execute(
            "UPDATE projects SET settings = $1 WHERE id = $2",
            json.dumps({"execution_environment": legacy, "theme": "dark"}),
            project,
        )
        home = uuid.uuid4()
        db.execute(
            "INSERT INTO sandbox_homes"
            " (id, created_at, updated_at, device_id, spec, project_id, topic_id,"
            " room_resource_id, resource_id, active_at)"
            " VALUES ($1, now(), now(), 'enrolled', $2, $3, $4, 'room-files', 'agent-files', now())",
            home,
            legacy["spec"],
            project,
            room,
        )

        db.upgrade(CLEANUP)

        assert dict(
            db.fetchrow(
                "SELECT device_id, name, token, owner_user_id, team_id FROM device WHERE device_id = 'enrolled'"
            )
        ) == {
            "device_id": "enrolled",
            "name": "Workstation",
            "token": "device-token",
            "owner_user_id": owner,
            "team_id": team,
        }
        assert (
            db.fetchval(
                "SELECT owner_user_id FROM hosted_device WHERE device_id = 'enrolled'"
            )
            == owner
        )
        wanted = {"kind": "sandbox", "spec": "team_devices"}
        environments = {
            row["agent_handle"]: json.loads(row["environment"])
            for row in db.fetch(
                "SELECT agent_handle, environment FROM agent_environments"
            )
        }
        assert environments == {"local": wanted, "cloud": cloud, "machine": machine}
        assert json.loads(
            db.fetchval(
                "SELECT chosen_by FROM agent_environments WHERE agent_handle = 'local'"
            )
        ) == {"kind": "user", "user_id": 1}
        for table, item in (("topics", room), ("tasks", task)):
            assert (
                json.loads(
                    db.fetchval(
                        f"SELECT default_environment FROM {table} WHERE id = $1", item
                    )
                )
                == wanted
            )
        assert json.loads(
            db.fetchval("SELECT settings FROM projects WHERE id = $1", project)
        ) == {
            "execution_environment": wanted,
            "theme": "dark",
        }
        assert dict(
            db.fetchrow(
                "SELECT id, device_id, spec, room_resource_id, resource_id FROM sandbox_homes WHERE id = $1",
                home,
            )
        ) == {
            "id": home,
            "device_id": "enrolled",
            "spec": "team_devices",
            "room_resource_id": "room-files",
            "resource_id": "agent-files",
        }
        assert db.fetchval("SELECT to_regclass('device_team')") is None
        assert db.fetchval("SELECT to_regclass('device_project')") is None
        assert (
            db.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE tgname IN"
                " ('device_legacy_team_write', 'device_enrollment_team_default')"
            )
            == 0
        )
        assert (
            db.fetchval(
                "SELECT count(*) FROM pg_proc WHERE proname IN"
                " ('device_legacy_team_write', 'device_enrollment_team_default', 'device_enrollment_personal_team')"
            )
            == 0
        )
