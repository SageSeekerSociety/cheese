"""8103084f5e42: every existing task is applied for, as it already was.

Before this migration every claim of every task was created pending and waited
for the publisher, with the claim's deadline set when it was approved; there
was no way to start directly. ``APPLY`` is that behaviour, so every existing
task becomes ``APPLY`` and keeps working as it did.

The migration runs while the previous release still serves. That release does
not map ``start_mode``: it reads the columns it knows and inserts tasks without
the new one, which must still work and give a task that behaves as that
release expects (``APPLY``).
"""

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "2b398ee3e416"
AFTER = "8103084f5e42"

#: The task columns the previous release reads and writes (its ``Task`` mapping
#: had no ``start_mode``).
OLD_COLUMNS = (
    "id, name, submitter_type, resubmittable, editable, intro, description,"
    " space_id, category_id, creator_id, default_deadline, deadline, approved,"
    " participant_limit, require_real_name, reject_reason, access_control_enabled,"
    " team_locking_policy, created_at, updated_at"
)


#: The rows this test seeds (the database at ``BEFORE`` carries demo tasks too).
OURS = "WHERE id BETWEEN 91001 AND 91003"


def _task(db: ReplayDatabase, task_id: int, *, submitter_type: int, approved: int):
    """A task as the previous release writes it: none of the new columns."""
    db.execute(
        "INSERT INTO task (id, name, submitter_type, resubmittable, editable,"
        " intro, description, space_id, category_id, creator_id,"
        " default_deadline, approved, require_real_name, reject_reason,"
        " access_control_enabled, team_locking_policy, created_at, updated_at)"
        " VALUES ($1, '题', $2, true, true, '', '', 1, 1, 1, 14, $3, false, '',"
        " false, 'NO_LOCK', now(), now())",
        task_id,
        submitter_type,
        approved,
    )


def test_every_existing_task_is_applied_for_and_the_old_release_still_reads_and_writes():
    with database_at(BEFORE) as db:
        _task(db, 91001, submitter_type=0, approved=0)  # a person's task, approved
        _task(db, 91002, submitter_type=1, approved=0)  # a team's task
        _task(db, 91003, submitter_type=0, approved=2)  # still waiting for review
        before = db.fetch(f"SELECT {OLD_COLUMNS} FROM task {OURS} ORDER BY id")

        db.upgrade(AFTER)

        modes = db.fetch(f"SELECT id, start_mode FROM task {OURS} ORDER BY id")
        assert [(r["id"], r["start_mode"]) for r in modes] == [
            (91001, "APPLY"),
            (91002, "APPLY"),
            (91003, "APPLY"),
        ]
        # The previous release reads exactly what it read before.
        assert db.fetch(f"SELECT {OLD_COLUMNS} FROM task {OURS} ORDER BY id") == before
        # It inserts a task without the new column; the task is applied for.
        _task(db, 91104, submitter_type=0, approved=2)
        assert db.fetchval("SELECT start_mode FROM task WHERE id = 91104") == "APPLY"
        # Only the two start modes are ever stored.
        try:
            db.execute("UPDATE task SET start_mode = 'LATER' WHERE id = 91104")
        except Exception as exc:  # asyncpg.CheckViolationError
            assert "ck_task_start_mode" in str(exc)
        else:
            raise AssertionError("a start mode outside DIRECT/APPLY was stored")

        db.downgrade(BEFORE)
        assert db.fetch(f"SELECT {OLD_COLUMNS} FROM task {OURS} ORDER BY id") == before
