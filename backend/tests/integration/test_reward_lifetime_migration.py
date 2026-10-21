"""What e4a7c2d9b6f1 does to reward packs issued before it.

The rules, as stated before the migration was written:

- every reward pack can be spent for a year after it was issued, whatever
  earned it: one that never expired and one that expired after 90 days both
  end up expiring a year after they were issued;
- a pack an administrator granted by hand keeps the expiry it was given,
  including none.
"""

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "53f2f3deeb83"
AFTER = "e4a7c2d9b6f1"
ISSUED = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


def _seed(db: ReplayDatabase) -> int:
    async def work(conn: asyncpg.Connection) -> int:
        n = uuid.uuid4().int % 1_000_000
        team_id = await conn.fetchval(
            "INSERT INTO team (id, name, handle, intro, description, avatar_id,"
            " created_at, updated_at) VALUES ($1, $2, $2, '', '', 1, now(), now())"
            " RETURNING id",
            1_960_000_000 + n,
            f"lifetime-{n}",
        )
        await conn.executemany(
            "INSERT INTO compute_grants (id, team_id, source, reason, credits_total,"
            " credits_used, expires_at, created_at, updated_at) VALUES"
            " (gen_random_uuid(), $1, $2, $3, 100, 0, $4, $5, $5)",
            [
                (team_id, "reward", "Beta participation", None, ISSUED),
                (
                    team_id,
                    "reward",
                    "Invitation: first project",
                    ISSUED + timedelta(days=90),
                    ISSUED,
                ),
                (team_id, "admin_grant", "Hackathon prize", None, ISSUED),
                (
                    team_id,
                    "admin_grant",
                    "Course credit",
                    ISSUED + timedelta(days=30),
                    ISSUED,
                ),
            ],
        )
        return team_id

    return db.run(work)


def test_reward_packs_expire_a_year_after_issue_and_admin_grants_keep_theirs():
    with database_at(BEFORE) as db:
        team_id = _seed(db)
        db.upgrade(AFTER)

        expiry = dict(
            db.fetch(
                "SELECT reason, expires_at FROM compute_grants WHERE team_id = $1",
                team_id,
            )
        )

    year = ISSUED + timedelta(days=365)
    assert expiry == {
        "Beta participation": year,
        "Invitation: first project": year,
        "Hackathon prize": None,
        "Course credit": ISSUED + timedelta(days=30),
    }
