"""ed60b2fceb51 on submissions handed in before entries kept their name.

A fresh database has no submissions, so `alembic upgrade head` alone proves
nothing about the backfill. This builds a database at the revision before,
writes submissions in the old shape (entries with no name of their own), then
upgrades and reads back the name each entry is now shown under.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg

from tests.conftest import _PG_BASE, _admin_recreate_db

BEFORE = "a3f70c5e9b21"
AFTER = "ed60b2fceb51"
BACKEND = Path(__file__).resolve().parents[2]

TASK = 9_000_001
OTHER_TASK = 9_000_002


def _alembic(url: str, target: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def _seed(conn) -> None:
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
        " updated_at) VALUES ($1, $2, 1, $3, 0, false, '', '', 'PENDING_REVIEW',"
        " now(), now())",
        TASK,
        TASK,
        uuid.uuid4(),
    )
    await conn.execute(
        "INSERT INTO task_submission (id, membership_id, version, submitter_id,"
        " created_at, updated_at) VALUES ($1, $2, 1, 1, now(), now())",
        TASK,
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
    name = "entry_prompt_migration_" + uuid.uuid4().hex[:12]
    url = f"{_PG_BASE}/{name}"
    dsn = url.replace("+asyncpg", "")
    asyncio.run(_admin_recreate_db(name))

    async def run(step):
        conn = await asyncpg.connect(dsn)
        try:
            return await step(conn)
        finally:
            await conn.close()

    try:
        _alembic(url, BEFORE)
        asyncio.run(run(_seed))
        _alembic(url, AFTER)

        rows = asyncio.run(
            run(
                lambda conn: conn.fetch(
                    'SELECT "index", prompt FROM task_submission_entry'
                    ' WHERE task_submission_id = $1 ORDER BY "index"',
                    TASK,
                )
            )
        )

        assert [(r["index"], r["prompt"]) for r in rows] == [
            (0, "Essay"),
            (1, None),
            (2, None),
        ]
    finally:

        async def drop():
            conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(drop())
