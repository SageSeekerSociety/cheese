"""The old machine choice is dropped once nothing reads it (migration
c4e9a2f7d153).

The rules, as stated before the migration was written:

- a choice the outgoing release wrote after the backfill first ran still
  becomes an environment, so nobody's room or agent loses what was picked in
  the last minutes before the deploy;
- the old columns, the room pin and the old settings keys are gone, and what
  sits next to them (the project's other settings, a session's generation) is
  kept;
- a sandbox home the outgoing release placed on a host after the homes were
  first copied is still kept, so its session is not placed twice;
- going back down restores the shape the previous release expects.
"""

import json
import uuid
from collections.abc import Iterator

import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "d7a3e5b1c829"
AFTER = "c4e9a2f7d153"

CLOUD = {"name": None, "profile": "cloud", "device_id": None, "whole_machine": False}
STANDARD = {"kind": "sandbox", "spec": "standard"}


def _columns(db: ReplayDatabase, table: str) -> set[str]:
    rows = db.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
        table,
    )
    return {row["column_name"] for row in rows}


def _table_exists(db: ReplayDatabase, table: str) -> bool:
    return db.fetchval("SELECT to_regclass($1) IS NOT NULL", table)


@pytest.fixture(scope="module")
def migrated() -> Iterator[tuple[ReplayDatabase, dict]]:
    with database_at(BEFORE) as db:
        # Written by the outgoing release after the backfill had already run.
        project, room = seed_room(db)
        db.execute(
            "UPDATE topics SET compute_config = $1::json WHERE id = $2",
            json.dumps(CLOUD),
            room,
        )
        db.execute(
            "INSERT INTO agent_sessions (id, conversation_id, agent_handle, harness,"
            " execution_request, created_at, updated_at)"
            " VALUES ($1, $2, 'late', 'claude-code', $3, now(), now())",
            uuid.uuid4(),
            room,
            json.dumps(
                {"generation": "g", "choice": CLOUD, "authorized_by": {"via": "web"}}
            ),
        )
        db.execute(
            "UPDATE projects SET settings = $1::json WHERE id = $2",
            json.dumps(
                {
                    "compute_configs": {"default": CLOUD},
                    "compute_profile": "cloud",
                    "x": 1,
                }
            ),
            project,
        )
        host, home = uuid.uuid4(), uuid.uuid4()
        db.execute(
            "INSERT INTO cloud_hosts (id, customer_id, account_id, offering_id,"
            " hostname, login_user, cores, memory_mb, disk_gb, status, ai_mode,"
            " ai_status, created_at, updated_at) VALUES ($1, 1, 1, 1, 'h',"
            " 'cheese', 4, 16384, 40, 'running', 'none', 'disabled', now(), now())",
            host,
        )
        db.execute(
            "INSERT INTO cloud_host_homes (id, host_id, project_id, topic_id,"
            " room_resource_id, resource_id, session_id, active_at, created_at,"
            " updated_at) VALUES ($1, $2, $3, $4, 'room', 'dir', $5, now(), now(),"
            " now())",
            home,
            host,
            project,
            room,
            uuid.uuid4(),
        )
        db.upgrade(AFTER)
        yield db, {"project": project, "room": room, "home": home}


def test_a_choice_written_after_the_backfill_still_becomes_an_environment(migrated):
    db, seen = migrated
    default = db.fetchval(
        "SELECT default_environment FROM topics WHERE id = $1", seen["room"]
    )
    assert json.loads(default) == STANDARD
    environment = db.fetchval(
        "SELECT environment FROM agent_environments"
        " WHERE conversation_id = $1 AND agent_handle = 'late'",
        seen["room"],
    )
    assert json.loads(environment) == STANDARD
    settings = json.loads(
        db.fetchval("SELECT settings FROM projects WHERE id = $1", seen["project"])
    )
    assert settings["execution_environment"] == STANDARD


def test_the_old_choice_is_gone_and_what_sat_beside_it_is_kept(migrated):
    db, seen = migrated
    assert not {"compute_config", "compute_profile"} & _columns(db, "topics")
    assert "compute_config" not in _columns(db, "tasks")
    assert not _table_exists(db, "device_topic")
    settings = json.loads(
        db.fetchval("SELECT settings FROM projects WHERE id = $1", seen["project"])
    )
    assert "compute_configs" not in settings
    assert "compute_profile" not in settings
    assert settings["x"] == 1
    request = json.loads(
        db.fetchval(
            "SELECT execution_request FROM agent_sessions WHERE agent_handle = 'late'"
        )
    )
    assert request == {"generation": "g"}


def test_a_home_placed_by_the_outgoing_release_is_kept(migrated):
    db, seen = migrated
    spec = db.fetchval("SELECT spec FROM sandbox_homes WHERE id = $1", seen["home"])
    assert spec == "standard"
    assert not _table_exists(db, "cloud_host_homes")


def test_going_back_down_restores_what_the_previous_release_expects(migrated):
    db, _ = migrated
    db.downgrade(BEFORE)
    try:
        assert {"compute_config", "compute_profile"} <= _columns(db, "topics")
        assert "compute_config" in _columns(db, "tasks")
        assert {"topic_id", "device_id", "visibility"} <= _columns(db, "device_topic")
    finally:
        db.upgrade(AFTER)
