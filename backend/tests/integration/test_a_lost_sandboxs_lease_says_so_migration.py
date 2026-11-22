"""2d06b1f1c765: a lease whose sandbox was destroyed or parked says so itself.

Before this release, only a key on the session (``sandbox_lost`` /
``sandbox_parked``) said the sandbox was gone, and its lease still read
``ready``. Now the lease says it (``released``) and the key is only the notice
the agent is owed. The rules, as a person would state them:

- A session whose sandbox is gone today reads as holding no machine after the
  release: its lease is released, and the notice it is owed stays.
- A session whose sandbox is there is left as it was.
"""

import json
import uuid

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "b7d2e4f19a60"
AFTER = "2d06b1f1c765"

LEASE = {
    "kind": "device",
    "device_id": "host-a",
    "status": "ready",
    "state": "/host-a/state",
    "verified_at": "2026-11-18T00:00:00+00:00",
}


def _session(
    db: ReplayDatabase, conversation: uuid.UUID, handle: str, request: dict | None
) -> uuid.UUID:
    row = uuid.uuid4()
    db.execute(
        "INSERT INTO agent_sessions (id, conversation_id, agent_handle, harness,"
        " work_lease, execution_request, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'claude-code', $4::json, $5::json, now(), now())",
        row,
        conversation,
        handle,
        json.dumps(LEASE),
        None if request is None else json.dumps(request),
    )
    return row


def _read(db: ReplayDatabase, row: uuid.UUID) -> tuple[dict, dict | None]:
    found = db.fetchrow(
        "SELECT work_lease, execution_request FROM agent_sessions WHERE id = $1", row
    )
    lease, request = found
    return json.loads(lease), None if request is None else json.loads(request)


def test_a_session_whose_sandbox_is_gone_holds_no_machine_and_is_still_owed_its_notice():
    with database_at(BEFORE) as db:
        _, room = seed_room(db)
        lost = _session(db, room, "lost", {"sandbox_lost": True, "generation": "g"})
        parked = _session(db, room, "parked", {"sandbox_parked": ["npm run dev"]})
        working = _session(db, room, "working", {"generation": "g"})

        db.upgrade(AFTER)

        for row in (lost, parked):
            lease, request = _read(db, row)
            assert lease["status"] == "released"
            assert lease["device_id"] == "host-a"
            assert "verified_at" not in lease
            assert request is not None
            assert request.get("sandbox_lost") or request.get("sandbox_parked")
        lease, request = _read(db, working)
        assert lease == LEASE
        assert request == {"generation": "g"}
