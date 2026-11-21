"""4c21cc2f20a4: every session's environment is filled from its lease.

The rules, as they stood before the migration was written:

- a session working on a machine keeps working there: its environment is the
  machine and directory its lease named, it is attached to it under the
  lease's generation (what its credential names), and the attachment is named
  by the session, so a command already running there is still recognised;
- a session whose sandbox was destroyed or parked still names where it was,
  as an environment nothing answers on, and its agent is still owed the
  notice;
- a conversation points to the place its work is in: the environment of its
  sessions that is of its default choice (one still answering before a
  released one, then the one placed last), and when none is, the one still
  answering of the session placed last, an agent on its owner's computer
  included; an agent on its owner's computer never displaces the default's;
- a personal conversation's environment is a standard sandbox;
- a directory a session left on a person's machine is still there for the
  cleanup to find, and nobody works in it;
- a thaw a tool call asked for is still asked for;
- a private chat's scratch container is no environment, and its running turn
  still finds it; what an install left that the platform does not name comes
  back as it was;
- running the reconciliation again changes nothing it already did, takes a
  session the previous release placed after it ran, and does not bring back
  an environment a session has left since.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    seed_room,
    seed_task,
)

BEFORE = "a7e2c4f19b30"
AFTER = "4c21cc2f20a4"

STANDARD = {"kind": "sandbox", "spec": "standard"}


def _lease(device: str, generation: str, **more) -> dict:
    return {
        "kind": "device",
        "device_id": device,
        "session_id": "ignored",
        "generation": generation,
        "resource_id": str(uuid.uuid4()),
        "status": "ready",
        "home": f"/{device}/home",
        "state": f"/{device}/state",
        "workspace": "/workspace",
        "url": f"http://{device}",
        "platform": True,
        "verified_at": "2026-11-18T00:00:00+00:00",
        **more,
    }


def _session(
    db: ReplayDatabase,
    conversation: uuid.UUID,
    handle: str,
    *,
    lease: dict | None,
    request: dict | None = None,
    placed: datetime | None = None,
    channel: str = "central",
) -> uuid.UUID:
    row = uuid.uuid4()
    db.execute(
        "INSERT INTO agent_sessions (id, conversation_id, agent_handle, harness,"
        " work_lease, execution_request, runtime_location, placed_at,"
        " created_at, updated_at)"
        " VALUES ($1, $2, $3, 'claude-code', $4::json, $5::json, $6::json, $7,"
        " now(), now())",
        row,
        conversation,
        handle,
        None if lease is None else json.dumps(lease),
        None if request is None else json.dumps(request),
        json.dumps({"device_id": "center", "channel": channel, "resource_id": "r"}),
        placed,
    )
    return row


def _choose(db: ReplayDatabase, conversation, handle, environment: dict) -> None:
    db.execute(
        "INSERT INTO agent_environments (conversation_id, agent_handle, environment,"
        " updated_at) VALUES ($1, $2, $3::jsonb, now())",
        conversation,
        handle,
        json.dumps(environment),
    )


def _home(db: ReplayDatabase, room: uuid.UUID, session: uuid.UUID) -> uuid.UUID:
    host = uuid.uuid4()
    db.execute(
        "INSERT INTO cloud_hosts (id, customer_id, account_id, offering_id,"
        " hostname, login_user, cores, memory_mb, disk_gb, status, ai_mode,"
        " ai_status, bootstrap_key, created_at, updated_at)"
        " VALUES ($1, 1, 1, 1, $2, 'cheese', 2, 4096, 20, 'running', 'none',"
        " 'disabled', 'k', now(), now())",
        host,
        f"host-{host.hex[:8]}",
    )
    home = uuid.uuid4()
    db.execute(
        "INSERT INTO sandbox_homes (id, host_id, topic_id, room_resource_id,"
        " resource_id, spec, session_id, active_at, frozen_at, created_at,"
        " updated_at) VALUES ($1, $2, $3, $4, 'frozen-one', 'standard', $5, now(),"
        " now(), now(), now())",
        home,
        host,
        room,
        str(room),
        session,
    )
    return home


def _personal(db: ReplayDatabase) -> uuid.UUID:
    conversation = uuid.uuid4()
    db.execute(
        "INSERT INTO conversations (id, project_id, kind, created_at)"
        " VALUES ($1, NULL, 'personal', now())",
        conversation,
    )
    return conversation


def _attachment(db: ReplayDatabase, session: uuid.UUID):
    return db.fetchrow(
        "SELECT a.id, a.generation, e.id AS environment_id, e.device_id,"
        " e.status, e.choice, e.executor, e.sandbox_home_id, e.resource_id"
        " FROM environment_attachments a JOIN environments e"
        " ON e.id = a.environment_id WHERE a.session_id = $1",
        session,
    )


def _pointed(db: ReplayDatabase, conversation: uuid.UUID):
    return db.fetchval(
        "SELECT environment_id FROM conversation_environments"
        " WHERE conversation_id = $1",
        conversation,
    )


def _notice(db: ReplayDatabase, session: uuid.UUID):
    found = db.fetchval(
        "SELECT environment_notice FROM agent_sessions WHERE id = $1", session
    )
    return None if found is None else json.loads(found)


def test_every_session_keeps_working_where_its_lease_said():
    now = datetime.now(UTC)
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = seed_task(db, project, room)
        # Two agents of the room's default choice, one placed after the
        # other, and an agent on its owner's computer placed last of all.
        earlier = _session(
            db,
            room,
            "earlier",
            # What an install left that the platform does not name comes back
            # as it was.
            lease=_lease(
                "box-1",
                "g1",
                context_tree={"files": 3},
                resolver="pypi",
                mcp_path="/mcp",
            ),
            placed=now,
        )
        # A private chat's scratch container: no environment, still where
        # its running turn finds it.
        scratch = {
            "kind": "private",
            "device_id": "center",
            "home": "/scratch",
            "command": ["run"],
            "topic": str(room),
            "image": "scratch:1",
        }
        private = _session(db, room, "private", lease=scratch, placed=now)
        later = _session(
            db,
            room,
            "later",
            lease=_lease("box-2", "g2"),
            request={"generation": "g2", "sandbox_thaw": "frozen-one"},
            placed=now + timedelta(minutes=1),
        )
        home = _home(db, room, later)
        owner = _session(
            db,
            room,
            "owner",
            lease=_lease("laptop", "g3", platform=False),
            placed=now + timedelta(minutes=2),
            channel="owner",
        )
        _choose(db, room, "owner", {"kind": "self_hosted", "device_id": "laptop"})
        # A task whose only agent's sandbox was parked.
        parked = _session(
            db,
            task,
            "parked",
            lease=_lease("box-3", "g4", status="released"),
            request={"generation": "g4", "sandbox_parked": ["npm run dev"]},
            placed=now,
        )
        lost = _session(
            db,
            task,
            "lost",
            lease=None,
            request={
                "generation": "g5",
                "sandbox_lost": True,
                "sandbox_lost_processes": ["python serve.py"],
                # It switched to its owner's computer and left its directory
                # on a person's machine.
                "retained_leases": [_lease("desk", "g0", platform=False)],
            },
        )
        # A task whose only agent works on its owner's computer.
        alone = seed_task(db, project, room)
        on_laptop = _session(
            db,
            alone,
            "owner",
            lease=_lease("laptop-2", "g7", platform=False),
            placed=now,
            channel="owner",
        )
        _choose(db, alone, "owner", {"kind": "self_hosted", "device_id": "laptop-2"})
        personal = _personal(db)
        mine = _session(db, personal, "cheese", lease=_lease("box-4", "g6"))

        db.upgrade(AFTER)

        for session, generation in ((earlier, "g1"), (later, "g2"), (owner, "g3")):
            found = _attachment(db, session)
            assert found is not None
            # Named as before, so a running command is still recognised, and
            # under the generation its credential names.
            assert found["id"] == session
            assert found["generation"] == generation
            assert found["status"] == "ready"
        assert _attachment(db, earlier)["device_id"] == "box-1"
        kept = json.loads(_attachment(db, earlier)["executor"])
        assert (kept["context_tree"], kept["resolver"], kept["mcp_path"]) == (
            {"files": 3},
            "pypi",
            "/mcp",
        )
        assert _attachment(db, private) is None
        location = json.loads(
            db.fetchval(
                "SELECT runtime_location FROM agent_sessions WHERE id = $1", private
            )
        )
        assert location["scratch"] == scratch
        assert json.loads(_attachment(db, later)["executor"])["url"] == "http://box-2"
        assert _attachment(db, later)["sandbox_home_id"] == home
        assert json.loads(_attachment(db, owner)["choice"]) == {
            "kind": "self_hosted",
            "device_id": "laptop",
        }
        # The room points to the one placed last of its default choice; the
        # owner's computer is never the room's.
        assert _pointed(db, room) == _attachment(db, later)["environment_id"]
        assert db.fetchval(
            "SELECT thaw_wanted_at IS NOT NULL FROM sandbox_homes WHERE id = $1", home
        )

        held = _attachment(db, parked)
        assert held["status"] == "released"
        assert held["device_id"] == "box-3"
        assert _pointed(db, task) == held["environment_id"]
        assert _notice(db, parked) == {"parked": ["npm run dev"]}
        assert _attachment(db, lost) is None
        assert _notice(db, lost) == {
            "lost": True,
            "lost_processes": ["python serve.py"],
        }
        left = db.fetch(
            "SELECT e.id FROM environments e WHERE e.device_id = 'desk'"
            " AND NOT EXISTS (SELECT 1 FROM environment_attachments a"
            "                  WHERE a.environment_id = e.id)"
            " AND NOT EXISTS (SELECT 1 FROM conversation_environments p"
            "                  WHERE p.environment_id = e.id)"
        )
        assert len(left) == 1

        found = _attachment(db, mine)
        assert json.loads(found["choice"]) == STANDARD
        assert _pointed(db, personal) == found["environment_id"]

        # Nobody uses the task's default: its work is on its agent's laptop.
        assert _pointed(db, alone) == _attachment(db, on_laptop)["environment_id"]


def test_running_it_again_takes_only_what_it_has_not_taken():
    with database_at(BEFORE) as db:
        _, room = seed_room(db)
        stays = _session(db, room, "stays", lease=_lease("box-1", "g1"))
        leaves = _session(
            db,
            room,
            "leaves",
            lease=_lease("box-2", "g2"),
            request={"retained_leases": [_lease("desk", "g0", platform=False)]},
        )
        db.upgrade(AFTER)
        before = db.fetchval("SELECT count(*) FROM environments")
        # The new release moves one session off its environment; the
        # previous release, still serving, places a session it had not.
        db.execute("DELETE FROM environment_attachments WHERE session_id = $1", leaves)
        late = _session(db, room, "late", lease=_lease("box-3", "g3"))

        db.downgrade(BEFORE)
        db.upgrade(AFTER)

        assert _attachment(db, stays)["generation"] == "g1"
        assert _attachment(db, leaves) is None
        assert _attachment(db, late)["device_id"] == "box-3"
        assert db.fetchval("SELECT count(*) FROM environments") == before + 1
