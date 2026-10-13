"""Own agents keep their connections when an account may hold one (0cfaea35ed30).

The rules, as stated before the migration was written:

- an own agent bound to a machine's login stays bound to it, with its name,
  and is bound to no connection of an account;
- one bound to nothing stays bound to nothing;
- an own agent is never bound to both a machine and an account's connection;
- going back, the agents on machines are as they were, and one that ran on an
  account's connection is left with none, as if its owner had removed it.
"""

import uuid

import asyncpg
import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "00cc083dc9f9"
AFTER = "0cfaea35ed30"


def _seed(db: ReplayDatabase, project: uuid.UUID) -> dict[str, uuid.UUID]:
    """Two own agents of one person: one on their laptop's login, one on
    nothing. Returns the instances, and the person and laptop."""
    seeded: dict = {}

    async def work(conn: asyncpg.Connection) -> None:
        n = uuid.uuid4().hex[:8]
        user_id = await conn.fetchval(
            'INSERT INTO "user" (username, email, created_at, updated_at)'
            " VALUES ($1::varchar, $1::varchar || '@example.test', now(), now())"
            " RETURNING id",
            f"keeper-{n}",
        )
        laptop = f"laptop-{n}"
        await conn.execute(
            "INSERT INTO device (device_id, name, token, owner_user_id,"
            " created_at, supply) VALUES ($1, $1, $2, $3, now(), 'self_hosted')",
            laptop,
            f"token-{laptop}",
            user_id,
        )
        for case, device in (("on_laptop", laptop), ("on_nothing", None)):
            instance = uuid.uuid4()
            await conn.execute(
                "INSERT INTO agent_instances (id, project_id, handle, configuration,"
                " display_name, name_source, is_active, created_at, updated_at)"
                " VALUES ($1, $2, $3, '{}', $4, 'human', true, now(), now())",
                instance,
                project,
                f"own-claude-code-{user_id}-{case}",
                f"{case}的 Claude Code（Max）",
            )
            await conn.execute(
                "INSERT INTO own_agents (instance_id, owner_user_id, harness,"
                " device_id) VALUES ($1, $2, 'claude-code', $3)",
                instance,
                user_id,
                device,
            )
            seeded[case] = instance
        seeded["user"], seeded["laptop"] = user_id, laptop

    db.run(work)
    return seeded


def _row(db: ReplayDatabase, instance: uuid.UUID) -> dict:
    row = db.fetchrow(
        "SELECT o.*, a.display_name FROM own_agents o"
        " JOIN agent_instances a ON a.id = o.instance_id WHERE o.instance_id = $1",
        instance,
    )
    assert row is not None
    return dict(row)


def test_own_agents_keep_their_connections_and_go_back_unchanged() -> None:
    with database_at(BEFORE) as db:
        project, _room = seed_room(db)
        seeded = _seed(db, project)
        before = {case: _row(db, seeded[case]) for case in ("on_laptop", "on_nothing")}

        db.upgrade(AFTER)

        for case, was in before.items():
            now = _row(db, seeded[case])
            assert now.pop("account_connection_id") is None, case
            assert now == was, case

        # Never both: an agent on the laptop cannot also take an account's.
        connection = uuid.uuid4()

        async def both(conn: asyncpg.Connection) -> None:
            await conn.execute(
                "INSERT INTO account_model_connections (id, owner_user_id, kind,"
                " key, key_last4, created_at, updated_at)"
                " VALUES ($1, $2, 'api_key', 'sealed', 'abcd', now(), now())",
                connection,
                seeded["user"],
            )
            await conn.execute(
                "UPDATE own_agents SET account_connection_id = $1"
                " WHERE instance_id = $2",
                connection,
                seeded["on_laptop"],
            )

        with pytest.raises(asyncpg.CheckViolationError):
            db.run(both)

        db.downgrade(BEFORE)

        for case, was in before.items():
            assert _row(db, seeded[case]) == was, case
