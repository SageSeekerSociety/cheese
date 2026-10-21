"""What b5d1e9a3c7f2 does to rewards issued before it.

The rules, as stated before the migration was written:

- an accepted-feedback reward earns as many points as credits; a beta reward
  earns none;
- the packs those rewards paid are filed as rewards, not administrator grants;
- a pack an administrator granted by hand stays an administrator grant.
"""

import uuid

import asyncpg

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "c3e8a1d5f240"
AFTER = "b5d1e9a3c7f2"


def _seed(db: ReplayDatabase) -> int:
    async def work(conn: asyncpg.Connection) -> int:
        n = uuid.uuid4().int % 1_000_000
        team_id = await conn.fetchval(
            "INSERT INTO team (id, name, handle, intro, description, avatar_id,"
            " created_at, updated_at) VALUES ($1, $2, $2, '', '', 1, now(), now())"
            " RETURNING id",
            1_950_000_000 + n,
            f"rewards-{n}",
        )
        await conn.execute(
            "INSERT INTO credit_rewards (key, team_id, credits, reason,"
            " actor_handle, created_at, updated_at) VALUES"
            " ('beta:1', $1, 500, 'Beta participation', '1', now(), now()),"
            " ('feedback:x', $1, 100, 'Accepted feedback FB-7', 'triager',"
            " now(), now())",
            team_id,
        )
        await conn.execute(
            "INSERT INTO compute_grants (id, team_id, source, reason, credits_total,"
            " credits_used, created_at, updated_at) VALUES"
            " (gen_random_uuid(), $1, 'admin_grant', 'Beta participation', 500, 0,"
            " now(), now()),"
            " (gen_random_uuid(), $1, 'admin_grant', 'Accepted feedback FB-7', 100,"
            " 0, now(), now()),"
            " (gen_random_uuid(), $1, 'admin_grant', 'Hackathon prize', 300, 0,"
            " now(), now())",
            team_id,
        )
        return team_id

    return db.run(work)


def test_earlier_rewards_get_their_points_and_their_packs_are_rewards():
    with database_at(BEFORE) as db:
        team_id = _seed(db)
        db.upgrade(AFTER)

        points = dict(
            db.fetch(
                "SELECT key, points FROM credit_rewards WHERE team_id = $1", team_id
            )
        )
        sources = dict(
            db.fetch(
                "SELECT reason, source FROM compute_grants WHERE team_id = $1",
                team_id,
            )
        )

    assert points == {"beta:1": 0, "feedback:x": 100}
    assert sources == {
        "Beta participation": "reward",
        "Accepted feedback FB-7": "reward",
        "Hackathon prize": "admin_grant",
    }
