"""Existing own agents keep working once each runs on one connection (00cc083dc9f9).

The rules, as stated before the migration was written:

- an own agent made before a person could have several is bound to the
  connection its owner used last: the most recently confirmed login still in
  use, ahead of a newer one its owner removed;
- it takes the name a new one gets, 「<昵称>的 Claude Code（<区分>）」, after that
  connection;
- one whose owner has a login only on a machine that is logged out is bound to
  it and keeps its name; the room says so when it is called;
- one whose owner has no login anywhere stays unbound, with its name.
"""

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "b7e4c2a9d5f1"
AFTER = "00cc083dc9f9"

_NOW = datetime.now(UTC)


def _seed(db: ReplayDatabase, project: uuid.UUID) -> dict[str, dict]:
    """One person per case, each with one own agent in ``project`` and the
    logins listed: (machine, logged in, removed, minutes ago, method, plan)."""
    cases = {
        "two_machines": [
            ("old", True, False, 30, "claude.ai", "max"),
            ("removed", False, True, 1, "claude.ai", "pro"),
            ("logged_out", False, False, 5, None, None),
        ],
        "logged_out_only": [("only", False, False, 10, None, None)],
        "no_login": [],
    }
    seeded: dict[str, dict] = {}

    async def work(conn: asyncpg.Connection) -> None:
        for case, logins in cases.items():
            n = uuid.uuid4().hex[:8]
            user_id = await conn.fetchval(
                'INSERT INTO "user" (username, email, created_at, updated_at)'
                " VALUES ($1::varchar, $1::varchar || '@example.test', now(), now())"
                " RETURNING id",
                f"{case[:12]}-{n}",
            )
            instance = uuid.uuid4()
            await conn.execute(
                "INSERT INTO agent_instances (id, project_id, handle, configuration,"
                " display_name, name_source, is_active, created_at, updated_at)"
                " VALUES ($1, $2, $3, '{}', $4, 'human', true, now(), now())",
                instance,
                project,
                f"own-claude-code-{user_id}",
                f"{case}的 Claude Code",
            )
            await conn.execute(
                "INSERT INTO own_agents (instance_id, owner_user_id, harness)"
                " VALUES ($1, $2, 'claude-code')",
                instance,
                user_id,
            )
            devices = {}
            for machine, logged_in, removed, ago, method, plan in logins:
                device_id = f"{machine}-{n}"
                await conn.execute(
                    "INSERT INTO device (device_id, name, token, owner_user_id,"
                    " created_at, supply) VALUES ($1, $1, $2, $3, now(),"
                    " 'self_hosted')",
                    device_id,
                    f"token-{device_id}",
                    user_id,
                )
                await conn.execute(
                    "INSERT INTO hosted_device (device_id, owner_user_id)"
                    " VALUES ($1, $2)",
                    device_id,
                    user_id,
                )
                await conn.execute(
                    "INSERT INTO device_claude_login (device_id, installed,"
                    " logged_in, auth_method, subscription_type, checked_at,"
                    " revoked_at) VALUES ($1, true, $2, $3, $4, $5, $6)",
                    device_id,
                    logged_in,
                    method,
                    plan,
                    _NOW - timedelta(minutes=ago),
                    _NOW if removed else None,
                )
                devices[machine] = device_id
            seeded[case] = {"instance": instance, "devices": devices}

    db.run(work)
    return seeded


def test_existing_own_agents_run_on_their_owners_last_connection() -> None:
    with database_at(BEFORE) as db:
        project, _room = seed_room(db)
        seeded = _seed(db, project)

        db.upgrade(AFTER)

        def bound(case: str) -> tuple[str | None, str]:
            row = db.fetchrow(
                "SELECT o.device_id, a.display_name FROM own_agents o"
                " JOIN agent_instances a ON a.id = o.instance_id"
                " WHERE o.instance_id = $1",
                seeded[case]["instance"],
            )
            assert row is not None
            return row["device_id"], row["display_name"]

        assert bound("two_machines") == (
            seeded["two_machines"]["devices"]["old"],
            "two_machines的 Claude Code（Max）",
        )
        assert bound("logged_out_only") == (
            seeded["logged_out_only"]["devices"]["only"],
            "logged_out_only的 Claude Code",
        )
        assert bound("no_login") == (None, "no_login的 Claude Code")

        db.downgrade(BEFORE)
        assert (
            db.fetchval(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'own_agents' AND column_name = 'device_id'"
            )
            == 0
        )
