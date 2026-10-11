"""Each old machine choice becomes the execution environment that replaces it
(migration 8b2f4d6a1c37).

The rules, as stated before the migration was written:

- the cloud becomes a standard sandbox, and the cloud with a whole machine a
  VM sandbox;
- a person's computer a room was given whole runs its agents there unisolated;
- any other person's computer, named or the one the room was pinned to, keeps
  its agents on that person's computers, in sandboxes;
- a device left to the platform with nothing saying which gives no
  environment, so the default applies;
- an agent's environment comes from its session's choice; a project's default
  from its saved default;
- a lease on one of the platform's machines is marked as such, so no page
  shows that machine.
"""

import json
import uuid
from collections.abc import Iterator

import pytest

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    seed_room,
    seed_task,
)

BEFORE = "5e1a7c3b9d24"
AFTER = "8b2f4d6a1c37"

CLOUD = {"name": None, "profile": "cloud", "device_id": None, "whole_machine": False}
WHOLE = {**CLOUD, "whole_machine": True}
STANDARD = {"kind": "sandbox", "spec": "standard"}
VM = {"kind": "sandbox", "spec": "vm"}


def _device(db: ReplayDatabase, name: str, owner: int) -> str:
    db.execute(
        "INSERT INTO device (device_id, name, token, owner_user_id, created_at,"
        " supply) VALUES ($1, $1, $1, $2, now(), 'self_hosted')",
        name,
        owner,
    )
    return name


def _on(name: str) -> dict:
    return {"name": name, "profile": "device", "device_id": name}


AUTO = {"name": None, "profile": "device", "device_id": None}


def _room(db: ReplayDatabase, choice, *, pinned=None, visibility="isolated"):
    project, room = seed_room(db)
    db.execute(
        "UPDATE topics SET compute_config = $1::json WHERE id = $2",
        json.dumps(choice),
        room,
    )
    if pinned is not None:
        db.execute(
            "INSERT INTO device_topic (topic_id, device_id, visibility)"
            " VALUES ($1, $2, $3)",
            room,
            pinned,
            visibility,
        )
    return project, room


def _session(db, conversation, handle, choice, lease_on=None):
    db.execute(
        "INSERT INTO agent_sessions (id, conversation_id, agent_handle, harness,"
        " execution_request, work_lease, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'claude-code', $4, $5, now(), now())",
        uuid.uuid4(),
        conversation,
        handle,
        json.dumps({"generation": "g", "choice": choice}),
        None if lease_on is None else json.dumps({"device_id": lease_on}),
    )


@pytest.fixture(scope="module")
def migrated() -> Iterator[tuple[ReplayDatabase, dict]]:
    with database_at(BEFORE) as db:
        owner = db.fetchval(
            'INSERT INTO "user" (username, email, created_at, updated_at)'
            " VALUES ('ada', 'ada@example.test', now(), now()) RETURNING id"
        )
        desk = _device(db, "desk", owner)
        box = _device(db, "box", owner)
        db.execute(
            "INSERT INTO device (device_id, name, token, owner_user_id, created_at,"
            " supply) VALUES ('pool', 'pool', 'pool', $1, now(), 'cloud')",
            owner,
        )
        seen = {"owner": owner}
        _, seen["cloud"] = _room(db, CLOUD)
        _, seen["whole"] = _room(db, WHOLE)
        _, seen["given_whole"] = _room(db, _on(desk), pinned=desk, visibility="host")
        _, seen["named"] = _room(db, _on(box), pinned=box)
        _, seen["auto_pinned"] = _room(db, AUTO, pinned=box)
        project, seen["auto"] = _room(db, AUTO)
        seen["task"] = seed_task(db, project, seen["given_whole"])
        db.execute(
            "UPDATE tasks SET compute_config = $1::json WHERE id = $2",
            json.dumps(AUTO),
            seen["task"],
        )
        _session(db, seen["cloud"], "on-cloud", CLOUD, lease_on="pool")
        _session(db, seen["given_whole"], "on-desk", _on(desk))
        _session(db, seen["auto"], "leased", AUTO, lease_on=box)
        _session(db, seen["auto"], "nowhere", AUTO)
        db.execute(
            "UPDATE projects SET settings = $1::json WHERE id = $2",
            json.dumps({"compute_configs": {"default": WHOLE}, "x": 1}),
            project,
        )
        seen["project"] = project
        db.upgrade(AFTER)
        yield db, seen


def _room_default(db, room):
    raw = db.fetchval("SELECT default_environment FROM topics WHERE id = $1", room)
    return None if raw is None else json.loads(raw)


def _agent(db, conversation, handle):
    raw = db.fetchval(
        "SELECT environment FROM agent_environments"
        " WHERE conversation_id = $1 AND agent_handle = $2",
        conversation,
        handle,
    )
    return None if raw is None else json.loads(raw)


def test_the_cloud_becomes_a_standard_sandbox_and_a_whole_machine_a_vm(migrated):
    db, seen = migrated
    assert _room_default(db, seen["cloud"]) == STANDARD
    assert _room_default(db, seen["whole"]) == VM
    assert _agent(db, seen["cloud"], "on-cloud") == STANDARD


def test_a_computer_given_whole_runs_its_agents_there_unisolated(migrated):
    db, seen = migrated
    there = {"kind": "self_hosted", "device_id": "desk"}
    assert _room_default(db, seen["given_whole"]) == there
    assert _agent(db, seen["given_whole"], "on-desk") == there


def test_any_other_persons_computer_keeps_its_agents_on_that_persons(migrated):
    db, seen = migrated
    theirs = {"kind": "sandbox", "spec": f"devices_of:{seen['owner']}"}
    assert _room_default(db, seen["named"]) == theirs
    assert _room_default(db, seen["auto_pinned"]) == theirs
    assert _agent(db, seen["auto"], "leased") == theirs


def test_a_device_left_to_the_platform_with_nothing_saying_which_has_none(migrated):
    db, seen = migrated
    assert _room_default(db, seen["auto"]) is None
    assert _agent(db, seen["auto"], "nowhere") is None


def test_a_task_left_to_the_platform_takes_its_rooms_pinned_computer(migrated):
    db, seen = migrated
    raw = db.fetchval(
        "SELECT default_environment FROM tasks WHERE id = $1", seen["task"]
    )
    assert json.loads(raw) == {"kind": "self_hosted", "device_id": "desk"}


def test_a_projects_default_is_copied_and_the_rest_of_its_settings_kept(migrated):
    db, seen = migrated
    settings = json.loads(
        db.fetchval("SELECT settings FROM projects WHERE id = $1", seen["project"])
    )
    assert settings["execution_environment"] == VM
    assert settings["x"] == 1


def test_a_lease_on_the_platforms_machine_is_marked_so(migrated):
    db, seen = migrated
    lease = json.loads(
        db.fetchval(
            "SELECT work_lease FROM agent_sessions WHERE agent_handle = 'on-cloud'"
        )
    )
    assert lease == {"device_id": "pool", "platform": True}
    leased = json.loads(
        db.fetchval(
            "SELECT work_lease FROM agent_sessions WHERE agent_handle = 'leased'"
        )
    )
    assert "platform" not in leased
