"""Duplicate pending rows are resolved before the one-pending index (b7e2c4a91f30).

A database deployed before the index can already hold two pending invitations
for the same person in the same team. The migration keeps the earliest of each
group, cancels the rest, leaves every other row as it was, and then refuses a
new duplicate. Seeded with raw SQL at the revision before it.
"""

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tests.integration.migration_replay import database_at

BEFORE = "a0f25891015d"
AFTER = "b7e2c4a91f30"
TEAM = 7_301_001
OTHER_TEAM = 7_301_002


def _seed(db, rows):
    async def work(conn):
        for team in (TEAM, OTHER_TEAM):
            await conn.execute(
                "INSERT INTO team (id, name, intro, description, avatar_id,"
                " created_at, updated_at, handle)"
                " VALUES ($1, 't', '', '', 0, now(), now(), $2)",
                team,
                f"pending-once-{team}",
            )
        for row_id, user, team, kind, status, at, deleted in rows:
            await conn.execute(
                "INSERT INTO team_membership_application"
                " (id, user_id, team_id, initiator_id, type, status, role, message,"
                "  created_at, updated_at, deleted_at)"
                " VALUES ($1, $2, $3, 1, $4, $5, 'MEMBER', '', $6, $6, $7)",
                row_id,
                user,
                team,
                kind,
                status,
                at,
                deleted,
            )

    db.run(work)


def test_duplicates_are_cancelled_but_the_earliest_and_new_ones_are_refused():
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    later = t0 + timedelta(minutes=1)
    with database_at(BEFORE) as db:
        _seed(
            db,
            [
                # Three pending for user 501 in TEAM: the earliest stays, though
                # its id is the largest — order is by when it was sent.
                (9_003, 501, TEAM, "INVITATION", "PENDING", t0, None),
                (9_001, 501, TEAM, "INVITATION", "PENDING", later, None),
                (9_002, 501, TEAM, "REQUEST", "PENDING", later, None),
                # Not part of the group: decided, deleted, another team, another person.
                (9_004, 501, TEAM, "INVITATION", "DECLINED", t0, None),
                (9_005, 501, TEAM, "INVITATION", "PENDING", t0, t0),
                (9_006, 501, OTHER_TEAM, "INVITATION", "PENDING", later, None),
                (9_007, 502, TEAM, "INVITATION", "PENDING", later, None),
            ],
        )

        db.upgrade(AFTER)

        rows = {
            r["id"]: (r["status"], r["processed_by_id"] is not None)
            for r in db.fetch(
                "SELECT id, status, processed_by_id FROM team_membership_application"
                " WHERE id >= 9000"
            )
        }
        assert rows == {
            9_003: ("PENDING", False),
            9_001: ("CANCELED", True),
            9_002: ("CANCELED", True),
            9_004: ("DECLINED", False),
            9_005: ("PENDING", False),
            9_006: ("PENDING", False),
            9_007: ("PENDING", False),
        }
        with pytest.raises(asyncpg.UniqueViolationError):
            db.execute(
                "INSERT INTO team_membership_application"
                " (id, user_id, team_id, initiator_id, type, status, role,"
                "  created_at, updated_at)"
                " VALUES (9008, 502, $1, 1, 'INVITATION', 'PENDING', 'MEMBER',"
                "  now(), now())",
                TEAM,
            )
