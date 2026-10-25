"""Duplicate claims a race left behind are retired, and the database keeps it
so from then on (migration 9e5088946704).

The rules, as stated before the migration was written:

- of one member's live, not rejected claims of one task, the approved one
  stays, else the newest; the others are withdrawn;
- a rejected claim beside a reapplication is left as it is, and so is a
  person and a team that happen to share a number;
- afterwards a second live, not rejected claim of the same member and task
  cannot be written; a rejected one beside a live one still can.
"""

import asyncpg
import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at
from tests.integration.test_task_attachments_move_migration import _task, _user

BEFORE = "168348b68d6c"
AFTER = "9e5088946704"


def _claim(
    db: ReplayDatabase,
    claim_id: int,
    *,
    task_id: int,
    member_id: int,
    approved: int,
    is_team: bool = False,
    days_ago: int = 0,
) -> None:
    db.execute(
        "INSERT INTO task_membership (id, task_id, member_id, participant_uuid,"
        " approved, is_team, email, phone, completion_status, created_at,"
        " updated_at) VALUES ($1, $2, $3, gen_random_uuid(), $4, $5, '', '',"
        " 'NOT_SUBMITTED', now() - make_interval(days => $6),"
        " now() - make_interval(days => $6))",
        claim_id,
        task_id,
        member_id,
        approved,
        is_team,
        days_ago,
    )


def _live(db: ReplayDatabase) -> set[int]:
    return {
        row["id"]
        for row in db.fetch("SELECT id FROM task_membership WHERE deleted_at IS NULL")
    }


def test_duplicate_claims_are_retired_and_cannot_come_back():
    with database_at(BEFORE) as db:
        _user(db, 501, "teacher")
        _task(db, 7001)
        # Member 11 claimed twice while pending, and once was approved.
        _claim(db, 1, task_id=7001, member_id=11, approved=2, days_ago=3)
        _claim(db, 2, task_id=7001, member_id=11, approved=0, days_ago=2)
        _claim(db, 3, task_id=7001, member_id=11, approved=2, days_ago=1)
        # Member 12: two pending claims; the newer stays.
        _claim(db, 4, task_id=7001, member_id=12, approved=2, days_ago=2)
        _claim(db, 5, task_id=7001, member_id=12, approved=2, days_ago=1)
        # Member 13: rejected, then reapplied.
        _claim(db, 6, task_id=7001, member_id=13, approved=1, days_ago=2)
        _claim(db, 7, task_id=7001, member_id=13, approved=2, days_ago=1)
        # A person and a team that share the number 14.
        _claim(db, 8, task_id=7001, member_id=14, approved=2)
        _claim(db, 9, task_id=7001, member_id=14, approved=2, is_team=True)

        db.upgrade(AFTER)

        assert _live(db) == {2, 5, 6, 7, 8, 9}
        with pytest.raises(asyncpg.UniqueViolationError):
            _claim(db, 10, task_id=7001, member_id=12, approved=2)
        # Rejecting again beside the live one is still allowed.
        _claim(db, 11, task_id=7001, member_id=13, approved=1)
