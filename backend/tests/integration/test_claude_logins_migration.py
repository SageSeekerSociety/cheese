"""A machine's login becomes its first login, and nobody logs in again
(e81c7f29ab40).

The rules, as stated before the migration was written:

- the login a machine already has is its first login, kept under the
  machine's id, with everything it said and what its owner named or removed;
- an own agent runs on the login it already ran on;
- while the previous release still serves, what it writes about a machine's
  login reaches that machine's first login, and an own agent it adds runs on
  that machine's first login;
- a login removed on this release stays out of use whatever the previous
  release writes about it afterwards.
"""

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "b3c41f7e9a20"
AFTER = "e81c7f29ab40"

_NOW = datetime.now(UTC)


def _person_with_machines(db: ReplayDatabase, project: uuid.UUID) -> dict:
    """A person with a named, logged-in laptop and an own agent on it, and a
    desk whose login they removed while it was away."""
    seeded: dict = {}

    async def work(conn: asyncpg.Connection) -> None:
        n = uuid.uuid4().hex[:8]
        user_id = await conn.fetchval(
            'INSERT INTO "user" (username, email, created_at, updated_at)'
            " VALUES ($1::varchar, $1::varchar || '@example.test', now(), now())"
            " RETURNING id",
            f"owner-{n}",
        )
        for machine, logged_in, removed, name in (
            ("laptop", True, False, "实验室的 Max"),
            ("desk", False, True, None),
        ):
            device_id = f"{machine}-{n}"
            await conn.execute(
                "INSERT INTO device (device_id, name, token, owner_user_id,"
                " created_at, supply) VALUES ($1, $1, $2, $3, now(), 'self_hosted')",
                device_id,
                f"token-{device_id}",
                user_id,
            )
            await conn.execute(
                "INSERT INTO device_claude_login (device_id, installed, logged_in,"
                " auth_method, subscription_type, name, checked_at, revoked_at)"
                " VALUES ($1, true, $2, 'claude.ai', 'max', $3, $4, $5)",
                device_id,
                logged_in,
                name,
                _NOW - timedelta(minutes=5),
                _NOW if removed else None,
            )
            seeded[machine] = device_id
        instance = uuid.uuid4()
        await conn.execute(
            "INSERT INTO agent_instances (id, project_id, handle, configuration,"
            " display_name, name_source, is_active, created_at, updated_at)"
            " VALUES ($1, $2, $3, '{}', 'x', 'human', true, now(), now())",
            instance,
            project,
            f"own-claude-code-{user_id}-{n}",
        )
        await conn.execute(
            "INSERT INTO own_agents (instance_id, owner_user_id, harness, device_id)"
            " VALUES ($1, $2, 'claude-code', $3)",
            instance,
            user_id,
            seeded["laptop"],
        )
        seeded["user_id"] = user_id
        seeded["instance"] = instance
        seeded["project"] = project

    db.run(work)
    return seeded


def _first_login(db: ReplayDatabase, device_id: str):
    return db.fetchrow(
        "SELECT * FROM claude_logins WHERE device_id = $1 AND local_id = 'default'",
        device_id,
    )


def test_a_machines_login_is_its_first_and_its_agent_stays_on_it() -> None:
    with database_at(BEFORE) as db:
        project, _room = seed_room(db)
        seeded = _person_with_machines(db, project)

        db.upgrade(AFTER)

        laptop = _first_login(db, seeded["laptop"])
        assert laptop["id"] == seeded["laptop"]
        assert laptop["logged_in"] is True
        assert laptop["subscription_type"] == "max"
        assert laptop["name"] == "实验室的 Max"
        desk = _first_login(db, seeded["desk"])
        assert desk["revoked_at"] is not None and desk["logged_in"] is False
        assert (
            db.fetchval(
                "SELECT login_id FROM own_agents WHERE instance_id = $1",
                seeded["instance"],
            )
            == seeded["laptop"]
        )


def test_what_the_previous_release_writes_reaches_the_first_login() -> None:
    with database_at(BEFORE) as db:
        project, _room = seed_room(db)
        seeded = _person_with_machines(db, project)
        db.upgrade(AFTER)

        # It hears the laptop again, now on another plan.
        db.execute(
            "UPDATE device_claude_login SET subscription_type = 'pro',"
            " checked_at = now() WHERE device_id = $1",
            seeded["laptop"],
        )
        laptop = _first_login(db, seeded["laptop"])
        assert laptop["subscription_type"] == "pro"
        assert laptop["name"] == "实验室的 Max", "a report does not touch the name"

        # A machine it hears for the first time.
        db.execute(
            "INSERT INTO device (device_id, name, token, owner_user_id, created_at,"
            " supply) VALUES ('new-one', 'new-one', 'token-new-one', $1, now(),"
            " 'self_hosted')",
            seeded["user_id"],
        )
        db.execute(
            "INSERT INTO device_claude_login (device_id, installed, logged_in,"
            " checked_at) VALUES ('new-one', true, true, now())"
        )
        assert _first_login(db, "new-one")["id"] == "new-one"

        # An own agent it adds on that machine.
        instance = uuid.uuid4()
        db.execute(
            "INSERT INTO agent_instances (id, project_id, handle, configuration,"
            " display_name, name_source, is_active, created_at, updated_at)"
            " VALUES ($1, $2, 'own-new', '{}', 'x', 'human', true, now(), now())",
            instance,
            seeded["project"],
        )
        db.execute(
            "INSERT INTO own_agents (instance_id, owner_user_id, harness, device_id)"
            " VALUES ($1, $2, 'claude-code', 'new-one')",
            instance,
            seeded["user_id"],
        )
        assert (
            db.fetchval(
                "SELECT login_id FROM own_agents WHERE instance_id = $1", instance
            )
            == "new-one"
        )


def test_a_login_removed_here_stays_removed_whatever_the_previous_release_writes() -> (
    None
):
    with database_at(BEFORE) as db:
        project, _room = seed_room(db)
        seeded = _person_with_machines(db, project)
        db.upgrade(AFTER)

        # Removed on this release; the old table knows nothing of it.
        db.execute(
            "UPDATE claude_logins SET revoked_at = now(), logged_in = false,"
            " name = NULL WHERE id = $1",
            seeded["laptop"],
        )
        # The previous release hears the machine, still logged in.
        db.execute(
            "UPDATE device_claude_login SET logged_in = true, checked_at = now()"
            " WHERE device_id = $1",
            seeded["laptop"],
        )

        laptop = _first_login(db, seeded["laptop"])
        assert laptop["revoked_at"] is not None
        assert laptop["logged_in"] is False
        assert laptop["name"] is None


def test_an_agent_on_a_further_login_stays_on_it() -> None:
    """Only a change of machine moves an agent: anything else the previous
    release writes to its row leaves the login it was added with."""
    with database_at(BEFORE) as db:
        project, _room = seed_room(db)
        seeded = _person_with_machines(db, project)
        db.upgrade(AFTER)
        db.execute(
            "INSERT INTO claude_logins (id, device_id, local_id, installed,"
            " logged_in, checked_at) VALUES ('second', $1, 'B7QX', true, true, now())",
            seeded["laptop"],
        )
        db.execute(
            "UPDATE own_agents SET login_id = 'second' WHERE instance_id = $1",
            seeded["instance"],
        )

        db.execute(
            "UPDATE own_agents SET callers = 'project' WHERE instance_id = $1",
            seeded["instance"],
        )

        assert (
            db.fetchval(
                "SELECT login_id FROM own_agents WHERE instance_id = $1",
                seeded["instance"],
            )
            == "second"
        )
