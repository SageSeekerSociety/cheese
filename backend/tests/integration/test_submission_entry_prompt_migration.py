"""ed60b2fceb51 on submissions handed in before entries kept their name.

A fresh database has no submissions, so `alembic upgrade head` alone proves
nothing about the backfill. This builds a database at the revision before,
writes a submission in the old shape (entries with no name of their own), then
upgrades and reads back the name each entry is now shown under.
"""

import uuid

import asyncpg

from tests.integration.migration_replay import database_at

BEFORE = "a3f70c5e9b21"
AFTER = "ed60b2fceb51"

TASK = 9_000_001
OTHER_TASK = 9_000_002


async def _seed(conn: asyncpg.Connection) -> None:
    # Only the submission tables matter here; the task and user rows they point
    # at are left out, so foreign keys are not checked while seeding.
    await conn.execute("SET session_replication_role = replica")
    await conn.executemany(
        'INSERT INTO task_submission_schema (task_id, "index", description, type)'
        " VALUES ($1, $2, $3, 0)",
        [
            (TASK, 0, "Essay"),
            (TASK, 1, ""),
            # Another task's form has an item at a position this task's lacks.
            (OTHER_TASK, 2, "Another task's item"),
        ],
    )
    await conn.execute(
        "INSERT INTO task_membership (id, task_id, member_id, participant_uuid,"
        " approved, is_team, email, phone, completion_status, created_at,"
        " updated_at) VALUES ($1, $1, 1, $2, 0, false, '', '', 'PENDING_REVIEW',"
        " now(), now())",
        TASK,
        uuid.uuid4(),
    )
    await conn.execute(
        "INSERT INTO task_submission (id, membership_id, version, submitter_id,"
        " created_at, updated_at) VALUES ($1, $1, 1, 1, now(), now())",
        TASK,
    )
    await conn.executemany(
        'INSERT INTO task_submission_entry (id, task_submission_id, "index",'
        " content_text, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, now(), now())",
        [
            (TASK + i, TASK, i, text)
            for i, text in enumerate(["an essay", "unnamed", "past the end"])
        ],
    )


def test_an_old_submission_is_named_by_the_form_as_it_reads_at_upgrade():
    with database_at(BEFORE) as db:
        db.run(_seed)
        db.upgrade(AFTER)

        rows = db.fetch(
            'SELECT "index", prompt FROM task_submission_entry'
            ' WHERE task_submission_id = $1 ORDER BY "index"',
            TASK,
        )

    assert [(r["index"], r["prompt"]) for r in rows] == [
        (0, "Essay"),
        (1, None),
        (2, None),
    ]
