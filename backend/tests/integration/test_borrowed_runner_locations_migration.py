"""A session row that points at another teammate's runner stops pointing at it.

Such rows were recorded by turns that ran as the project's default agent under
another teammate's seat. Recovery attached a reader for each row, so two
readers wrote one runner's mirror file. The migration (7e4b9d2c1a60) clears the
borrowed row's location, keeps its work lease, and leaves the runner's own row
alone.

The rows are seeded on the database of the revision before the migration, and
read back from it afterwards.
"""

import json
import uuid

from tests.integration.migration_replay import (
    database_at,
    seat_handle,
    seed_agent,
    seed_extra_room,
    seed_room,
)

BEFORE = "c4f1d8a2e9b7"
AFTER = "7e4b9d2c1a60"


def _location(room: uuid.UUID, seat: str, state: str) -> dict:
    return {
        "device_id": "device",
        "resource_id": str(room),
        "channel": "device",
        "runtime": {"harness": "claude-code", "agent_handle": seat, "state": state},
    }


def _session(db, room, handle, location, lease=None) -> uuid.UUID:
    row = uuid.uuid4()
    db.execute(
        "INSERT INTO agent_sessions (id, topic_id, agent_handle, harness,"
        " runtime_location, work_lease, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'claude-code', $4::json, $5::json, now(), now())",
        row,
        room,
        handle,
        json.dumps(location),
        None if lease is None else json.dumps(lease),
    )
    return row


def _read(db, row: uuid.UUID) -> tuple[dict | None, dict | None]:
    found = db.fetchrow(
        "SELECT runtime_location, work_lease FROM agent_sessions WHERE id = $1", row
    )
    return tuple(None if v is None else json.loads(v) for v in found)


def test_a_row_on_another_teammates_runner_is_no_longer_found():
    with database_at(BEFORE) as db:
        project, shared = seed_room(db)
        alone = seed_extra_room(db, project)
        seed_agent(db, project, "cheese")
        reviewer = seed_agent(db, project, "reviewer")
        seat = seat_handle(reviewer)
        lease = {"kind": "device", "device_id": "machine", "status": "ready"}

        # The runner's owner: the teammate's own session on its seat.
        owner = _session(
            db, shared, "reviewer", _location(shared, seat, "/state/shared"), lease
        )
        # The default agent's row, recorded on the teammate's runner.
        borrowed = _session(
            db, shared, "cheese", _location(shared, seat, "/state/shared"), lease
        )
        # A mismatch with nobody else on its runner is not this case.
        lonely = _session(db, alone, "cheese", _location(alone, seat, "/state/alone"))

        db.upgrade(AFTER)

        assert _read(db, borrowed) == (None, lease)
        assert _read(db, owner) == (_location(shared, seat, "/state/shared"), lease)
        assert _read(db, lonely) == (_location(alone, seat, "/state/alone"), None)
