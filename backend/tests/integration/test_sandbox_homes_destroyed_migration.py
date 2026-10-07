"""Home archives move to the kept list when idle sandboxes stop being archived
(migration c11a23e6ea8d).

The rules, as stated before the migration was written:

- every home archive in the private bucket is kept, listed with the
  conversation its session worked in, whether its host found it pushed, and
  a date 30 days on;
- the home rows that stood for an archive go, so the session's next tool call
  gets a new sandbox and is told so; a home on a host with no archive stays;
- a room cleanup that waited on an unpushed archive goes on.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import database_at, seed_room, seed_task

BEFORE = "7d3e1c4b9a20"
AFTER = "c11a23e6ea8d"


def test_home_archives_are_kept_thirty_days_and_their_homes_go():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = seed_task(db, project, room)
        in_room, in_task = uuid.uuid4(), uuid.uuid4()
        for session, conversation in ((in_room, room), (in_task, task)):
            db.execute(
                "INSERT INTO agent_sessions (id, conversation_id, agent_handle,"
                " harness, work_lease, created_at, updated_at)"
                " VALUES ($1, $2, 'cheese', 'claude-code', $3, now(), now())",
                session,
                conversation,
                json.dumps({}),
            )
        host = uuid.uuid4()
        db.execute(
            "INSERT INTO cloud_hosts (id, customer_id, account_id, offering_id,"
            " hostname, login_user, cores, memory_mb, disk_gb, status, ai_mode,"
            " ai_status, created_at, updated_at) VALUES ($1, 1, 1, 1, 'h', 'cheese',"
            " 4, 16384, 40, 'running', 'none', 'disabled', now(), now())",
            host,
        )

        def home(session, host_id, key, published):
            db.execute(
                "INSERT INTO cloud_host_homes (id, host_id, project_id, topic_id,"
                " room_resource_id, resource_id, session_id, archive_key,"
                " archive_size, archive_published, active_at, created_at,"
                " updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 13, $9,"
                " now(), now(), now())",
                uuid.uuid4(),
                host_id,
                project,
                room,
                str(room),
                str(uuid.uuid4()),
                session,
                key,
                published,
            )

        # Archived and on no host; one placed on a host to be restored; one
        # running there with no archive.
        home(in_room, None, "sandbox-archives/a.tar.gz", False)
        home(in_task, host, "sandbox-archives/b.tar.gz", True)
        home(None, host, None, None)
        cleanup = uuid.uuid4()
        db.execute(
            "INSERT INTO room_cleanups (id, project_id, topic_id, resource_id,"
            " due_at, state, resources, last_error, created_at, updated_at)"
            " VALUES ($1, $2, $3, $3, now() + interval '1 day', 'kept', '[]',"
            " 'an archived sandbox home holds work that was not pushed', now(), now())",
            cleanup,
            project,
            room,
        )

        db.upgrade(AFTER)

        kept = {
            row["key"]: row
            for row in db.fetch(
                "SELECT key, project_id, conversation_id, session_id, size,"
                " published, delete_after, told_at FROM retained_home_archives"
            )
        }
        assert set(kept) == {"sandbox-archives/a.tar.gz", "sandbox-archives/b.tar.gz"}
        a, b = kept["sandbox-archives/a.tar.gz"], kept["sandbox-archives/b.tar.gz"]
        assert (a["conversation_id"], a["session_id"], a["published"]) == (
            room,
            in_room,
            False,
        )
        # A task's session worked in the task's own conversation.
        assert (b["conversation_id"], b["published"]) == (task, True)
        assert a["size"] == 13 and a["told_at"] is None
        later = datetime.now(UTC) + timedelta(days=30)
        assert abs(a["delete_after"] - later) < timedelta(minutes=5)

        left = db.fetch("SELECT host_id, archive_key FROM cloud_host_homes")
        assert [(r["host_id"], r["archive_key"]) for r in left] == [(host, None)]
        told = {
            row["id"]: json.loads(row["execution_request"] or "{}")
            for row in db.fetch("SELECT id, execution_request FROM agent_sessions")
        }
        assert told[in_room] == {"sandbox_lost": True}
        assert told[in_task] == {"sandbox_lost": True}
        waiting = db.fetchrow(
            "SELECT state, last_error, due_at <= now() AS due FROM room_cleanups"
            " WHERE id = $1",
            cleanup,
        )
        assert (waiting["state"], waiting["last_error"], waiting["due"]) == (
            "pending",
            None,
            True,
        )
