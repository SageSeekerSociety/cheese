"""What d6a3f81c2e47 does to invite codes made before it.

The rules, as stated before the migration was written:

- a code gains one use for every friend on it who has already got started
  (accepted a first delivery in time);
- friends who registered but never got started give nothing back;
- a code nobody has got started on, and an administrator's code, keep their uses.
"""

import uuid
from datetime import UTC, datetime

import asyncpg

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "b7e4c9a21d68"
AFTER = "d6a3f81c2e47"
AT = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


def _seed(db: ReplayDatabase) -> dict[str, int]:
    async def work(conn: asyncpg.Connection) -> dict[str, int]:
        n = uuid.uuid4().hex[:8]

        async def person(name: str) -> int:
            return await conn.fetchval(
                'INSERT INTO "user" (username, email, created_at, updated_at)'
                " VALUES ($1::varchar, $1::varchar || '@example.test', now(), now())"
                " RETURNING id",
                f"{name}-{n}",
            )

        async def code(owner: int | None, max_uses: int, used: int) -> int:
            return await conn.fetchval(
                "INSERT INTO invite_code (code, max_uses, use_count, is_active,"
                " owner_user_id, note) VALUES ($1, $2, $3, true, $4, 'personal')"
                " RETURNING id",
                f"c-{uuid.uuid4().hex[:10]}",
                max_uses,
                used,
                owner,
            )

        busy, quiet = await person("busy"), await person("quiet")
        codes = {
            "busy": await code(busy, 4, 3),
            "quiet": await code(quiet, 1, 1),
            "admin": await code(None, 5, 2),
        }
        friends = [
            (busy, codes["busy"], AT),
            (busy, codes["busy"], AT),
            (busy, codes["busy"], None),
            (quiet, codes["quiet"], None),
        ]
        for inviter, code_id, activated in friends:
            await conn.execute(
                "INSERT INTO invite_referrals (invitee_user_id, inviter_user_id,"
                " code_id, registered_at, activated_at) VALUES ($1, $2, $3, $4, $5)",
                await person(f"friend{uuid.uuid4().hex[:6]}"),
                inviter,
                code_id,
                AT,
                activated,
            )
        return codes

    return db.run(work)


def test_each_friend_already_started_gives_one_seat_back():
    with database_at(BEFORE) as db:
        codes = _seed(db)
        db.upgrade(AFTER)
        uses = dict(
            db.fetch(
                "SELECT id, max_uses FROM invite_code WHERE id = ANY($1::int[])",
                list(codes.values()),
            )
        )

    assert {name: uses[i] for name, i in codes.items()} == {
        "busy": 6,
        "quiet": 1,
        "admin": 5,
    }
