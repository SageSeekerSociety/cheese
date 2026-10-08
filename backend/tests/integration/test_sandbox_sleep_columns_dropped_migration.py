"""The columns sleeping and archived homes kept go, and kept archives whose
notice was dropped are told again (migration a9c88363ec79).

The rules, as stated before the migration was written:

- a kept home archive whose conversation never received its notice (a task's
  or a 支线's, which the previous release marked told without placing one) is
  untold again, so the sandbox sweep tells it; one whose conversation did
  receive it stays told;
- a home is always on a host: its host column is required.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tests.integration.migration_replay import database_at, seed_room, seed_task

BEFORE = "c11a23e6ea8d"
AFTER = "a9c88363ec79"


def test_archives_whose_notice_was_dropped_are_told_again():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = seed_task(db, project, room)
        told = datetime.now(UTC) - timedelta(hours=1)
        for key, conversation in (("in-room", room), ("in-task", task)):
            db.execute(
                "INSERT INTO retained_home_archives (id, key, project_id,"
                " conversation_id, delete_after, told_at, created_at, updated_at)"
                " VALUES ($1, $2, $3, $4, now() + interval '30 days', $5, now(),"
                " now())",
                uuid.uuid4(),
                key,
                project,
                conversation,
                told,
            )
        db.execute(
            "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
            " author, content, refs, meta, created_at, updated_at)"
            " VALUES ($1, $2, $3, 'event', 'platform', 'system', 'kept', '[]',"
            " $4::json, now(), now())",
            uuid.uuid4(),
            project,
            room,
            json.dumps({"who": "platform", "event_type": "sandbox_archives_kept"}),
        )

        db.upgrade(AFTER)

        rows = {
            row["key"]: row["told_at"]
            for row in db.fetch("SELECT key, told_at FROM retained_home_archives")
        }
        assert rows["in-room"] == told
        assert rows["in-task"] is None
        with pytest.raises(asyncpg.NotNullViolationError):
            db.execute(
                "INSERT INTO cloud_host_homes (id, project_id, topic_id,"
                " room_resource_id, resource_id, active_at, created_at, updated_at)"
                " VALUES ($1, $2, $3, 'r', 'r', now(), now(), now())",
                uuid.uuid4(),
                project,
                room,
            )
