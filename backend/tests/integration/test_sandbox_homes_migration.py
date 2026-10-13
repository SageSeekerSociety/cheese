"""Every home on the platform's hosts is carried into the homes kept on any
machine (migration d7a3e5b1c829).

The rules, as stated before the migration was written:

- each home keeps its session, its room and its directory;
- a whole VM's home is a sandbox of spec `vm`, any other one `standard`.
"""

import uuid
from collections.abc import Iterator

import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "8b2f4d6a1c37"
AFTER = "d7a3e5b1c829"


def _host(db: ReplayDatabase, *, whole: bool) -> uuid.UUID:
    host = uuid.uuid4()
    db.execute(
        "INSERT INTO cloud_hosts (id, customer_id, account_id, offering_id,"
        " hostname, login_user, cores, memory_mb, disk_gb, status, ai_mode,"
        " ai_status, whole_machine, created_at, updated_at) VALUES ($1, 1, 1, 1,"
        " 'h', 'cheese', 4, 16384, 40, 'running', 'none', 'disabled', $2, now(),"
        " now())",
        host,
        whole,
    )
    return host


def _home(db, host, project, room, session) -> uuid.UUID:
    home = uuid.uuid4()
    db.execute(
        "INSERT INTO cloud_host_homes (id, host_id, project_id, topic_id,"
        " room_resource_id, resource_id, session_id, active_at, created_at,"
        " updated_at) VALUES ($1, $2, $3, $4, 'room', $5, $6, now(), now(), now())",
        home,
        host,
        project,
        room,
        str(home)[:36],
        session,
    )
    return home


@pytest.fixture(scope="module")
def migrated() -> Iterator[tuple[ReplayDatabase, dict]]:
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        shared, own = uuid.uuid4(), uuid.uuid4()
        seen = {
            "shared": _home(db, _host(db, whole=False), project, room, shared),
            "own": _home(db, _host(db, whole=True), project, room, own),
            "sessions": {"shared": shared, "own": own},
        }
        db.upgrade(AFTER)
        yield db, seen


def _copied(db, home):
    return db.fetchrow("SELECT * FROM sandbox_homes WHERE id = $1", home)


def test_each_home_keeps_its_session_room_and_directory(migrated):
    db, seen = migrated
    for name in ("shared", "own"):
        row = _copied(db, seen[name])
        assert row["session_id"] == seen["sessions"][name]
        assert row["room_resource_id"] == "room"
        assert row["resource_id"] == str(seen[name])[:36]
        assert row["device_id"] is None and row["host_id"] is not None


def test_a_whole_vms_home_is_a_vm_sandbox_and_any_other_a_standard_one(migrated):
    db, seen = migrated
    assert _copied(db, seen["own"])["spec"] == "vm"
    assert _copied(db, seen["shared"])["spec"] == "standard"
