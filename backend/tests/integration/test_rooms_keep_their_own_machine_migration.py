"""The migration binds rooms that already ran on an enrolled machine `host`.

A room on automatic selection was never bound to the enrolled machine its
sessions took, and saw the whole machine as every room did then. Now an
unbound room runs isolated, so without a binding it would lose what it had.

The rooms and sessions are seeded on the database of the revision before the
migration (4383bf20b465), and the room-to-machine bindings are read back from
it afterwards.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    seed_extra_room,
    seed_room,
)

BEFORE = "4383bf20b465"
AFTER = "d72d0f566149"


def _bindings(db: ReplayDatabase, rooms) -> dict:
    found = {
        row["topic_id"]: (row["device_id"], row["visibility"])
        for row in db.fetch("SELECT topic_id, device_id, visibility FROM device_topic")
    }
    return {room: found.get(room) for room in rooms}


def test_rooms_keep_the_enrolled_machine_they_ran_on():
    with database_at(BEFORE) as db:
        project, first_room = seed_room(db)
        automatic, moved, now_cloud, chose_cloud, named, never = (
            first_room,
            *(seed_extra_room(db, project) for _ in range(5)),
        )
        start = datetime(2026, 9, 1, tzinfo=UTC)

        owner = db.fetchval(
            'INSERT INTO "user" (username, email, created_at, updated_at)'
            " VALUES ('keeper', 'keeper@example.test', now(), now()) RETURNING id"
        )
        ids = {}
        for name, supply in (
            ("first", "self_hosted"),
            ("second", "self_hosted"),
            ("cloud", "cloud"),
        ):
            ids[name] = f"device-{name}"
            db.execute(
                "INSERT INTO device (device_id, name, token, owner_user_id,"
                " created_at, supply) VALUES ($1, $2, $3, $4, now(), $5)",
                ids[name],
                name,
                uuid.uuid4().hex,
                owner,
                supply,
            )

        def ran(room, handle, device, minutes):
            db.execute(
                "INSERT INTO agent_sessions (id, topic_id, agent_handle, harness,"
                " work_lease, placed_at, created_at, updated_at)"
                " VALUES ($1, $2, $3, 'claude-code', $4::json, $5, now(), now())",
                uuid.uuid4(),
                room,
                handle,
                json.dumps({"kind": "device", "device_id": ids[device]}),
                start + timedelta(minutes=minutes),
            )

        ran(automatic, "analyst", "first", 0)
        # Used both enrolled machines; works on the second now.
        ran(moved, "analyst", "first", 0)
        ran(moved, "builder", "second", 5)
        # Ran on an enrolled machine, then moved to Cloud.
        ran(now_cloud, "analyst", "first", 0)
        ran(now_cloud, "builder", "cloud", 5)
        ran(chose_cloud, "analyst", "first", 0)
        db.execute(
            "UPDATE topics SET compute_config = $1::json WHERE id = $2",
            json.dumps({"profile": "cloud", "name": None, "device_id": None}),
            chose_cloud,
        )
        # Named a machine and was bound isolated: it keeps that.
        ran(named, "analyst", "first", 0)
        db.execute(
            "INSERT INTO device_topic (topic_id, device_id, visibility)"
            " VALUES ($1, $2, 'isolated')",
            named,
            ids["first"],
        )

        db.upgrade(AFTER)

        rooms = (automatic, moved, now_cloud, chose_cloud, named, never)
        bound = _bindings(db, rooms)
        assert bound[automatic] == (ids["first"], "host")
        assert bound[moved] == (ids["second"], "host")
        assert bound[now_cloud] is None
        assert bound[chose_cloud] is None
        assert bound[named] == (ids["first"], "isolated")
        assert bound[never] is None

        # Running it again changes nothing.
        db.downgrade(BEFORE)
        db.upgrade(AFTER)
        assert _bindings(db, rooms) == bound
