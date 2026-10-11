"""Sessions the cloud channel placed are recovered by the one executor channel
(migration 76d8e010047a).

A restart recovers a session only if its place names the channel that recovers
it. After the cloud channel is gone, a session it placed must still be found,
with the rest of where it runs left as it was; a session that already named the
device channel is untouched, and so is one that has no place yet.
"""

import json
import uuid
from collections.abc import Iterator

import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "70c0b60a075a"
AFTER = "76d8e010047a"


def _session(db: ReplayDatabase, room: uuid.UUID, handle: str, place) -> uuid.UUID:
    session = uuid.uuid4()
    db.execute(
        "INSERT INTO agent_sessions (id, conversation_id, agent_handle, harness,"
        " runtime_location, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'claude-code', $4, now(), now())",
        session,
        room,
        handle,
        None if place is None else json.dumps(place),
    )
    return session


@pytest.fixture(scope="module")
def migrated() -> Iterator[tuple[ReplayDatabase, dict]]:
    with database_at(BEFORE) as db:
        _, room = seed_room(db)
        cloud_place = {
            "device_id": "center",
            "resource_id": str(room),
            "channel": "cloud",
            "session_id": "s1",
            "runtime": {"state": "/home/cheese/state"},
        }
        rows = {
            "cloud": _session(db, room, "cheese-a", cloud_place),
            "device": _session(
                db, room, "cheese-b", {**cloud_place, "channel": "device"}
            ),
            "unplaced": _session(db, room, "cheese-c", None),
        }
        db.upgrade(AFTER)
        yield db, {"place": cloud_place, "rows": rows}


def _place(db: ReplayDatabase, session: uuid.UUID):
    raw = db.fetchval(
        "SELECT runtime_location FROM agent_sessions WHERE id = $1", session
    )
    return None if raw is None else json.loads(raw)


def test_a_session_the_cloud_channel_placed_names_the_one_channel(migrated):
    db, seeded = migrated
    assert _place(db, seeded["rows"]["cloud"]) == {
        **seeded["place"],
        "channel": "device",
    }


def test_a_session_on_the_device_channel_is_untouched(migrated):
    db, seeded = migrated
    assert _place(db, seeded["rows"]["device"]) == {
        **seeded["place"],
        "channel": "device",
    }


def test_a_session_with_no_place_keeps_none(migrated):
    db, seeded = migrated
    assert _place(db, seeded["rows"]["unplaced"]) is None
