"""What e3f9a1c62b47 does to a personal team that was never one person's.

A user's personal team existed before anyone wrote the rule down (#2249), so
some of them hold a second person, a pending invitation, a join token, or an
open recruitment post. The migration ends the members, closes the applications,
clears the token, withdraws the ad, and then makes the rule hold in the
database: a *new* live row the trigger refuses.

The last case is the point of the exercise — a deliberate violation written
straight into the table, the way a script or a path added later would, must be
refused by the database itself and not by the service that happens to check.
"""

import uuid

import asyncpg
import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "a9c3e5f7b1d2"
AFTER = "e3f9a1c62b47"


def _seed(db: ReplayDatabase) -> dict[str, int]:
    """An owner, a friend, and a personal team the friend is wrongly in — with
    a pending invitation and a request aimed at it, and a join token on it."""

    async def work(conn: asyncpg.Connection) -> dict[str, int]:
        n = uuid.uuid4().hex[:8]

        async def person(name: str) -> int:
            return await conn.fetchval(
                'INSERT INTO "user" (username, email, created_at, updated_at)'
                " VALUES ($1::varchar, $1::varchar || '@example.test', now(), now())"
                " RETURNING id",
                f"{name}-{n}",
            )

        owner, friend = await person("owner"), await person("friend")
        team = await conn.fetchval(
            "INSERT INTO team (id, name, intro, description, avatar_id,"
            " personal_owner_user_id, created_at, updated_at)"
            " VALUES (nextval('team_seq'), '个人', '', '', 0, $1, now(), now())"
            " RETURNING id",
            owner,
        )
        for user, role in ((owner, 0), (friend, 1)):
            await conn.execute(
                "INSERT INTO team_user_relation (id, team_id, user_id, role,"
                " created_at, updated_at)"
                " VALUES (nextval('team_user_relation_seq'), $1, $2, $3, now(), now())",
                team,
                user,
                role,
            )
        for user, type_ in ((friend, "INVITATION"), (friend, "REQUEST")):
            await conn.execute(
                "INSERT INTO team_membership_application (id, user_id, team_id,"
                " initiator_id, type, status, role, message, created_at, updated_at)"
                " VALUES (nextval('team_membership_application_seq'), $1, $2, $3,"
                " $4, 'PENDING', 'MEMBER', '', now(), now())",
                user,
                team,
                owner,
                type_,
            )
        await conn.execute(
            "UPDATE team SET join_token = $2 WHERE id = $1",
            team,
            f"token-{n}",
        )
        # An ad the owner put up asking people to join it. The plaza lists
        # posts by status alone, so an OPEN one keeps offering this team.
        await conn.execute(
            "INSERT INTO team_recruitment_post (team_id, title, content,"
            " status, created_by, created_at, updated_at)"
            " VALUES ($1, 'Join us', 'Please', 'OPEN', $2, now(), now())",
            team,
            owner,
        )
        return {"owner": owner, "friend": friend, "team": team}

    return db.run(work)


def _live_members(db: ReplayDatabase, team: int) -> list[tuple[int, int]]:
    return [
        (row["user_id"], row["role"])
        for row in db.fetch(
            "SELECT user_id, role FROM team_user_relation"
            " WHERE team_id = $1 AND deleted_at IS NULL ORDER BY user_id",
            team,
        )
    ]


def test_the_extra_member_is_ended_and_the_owner_stays():
    with database_at(BEFORE) as db:
        seeded = _seed(db)
        assert _live_members(db, seeded["team"]) == [
            (seeded["owner"], 0),
            (seeded["friend"], 1),
        ]

        db.upgrade(AFTER)

        assert _live_members(db, seeded["team"]) == [(seeded["owner"], 0)]
        # Ended, not deleted: the row is still there as history.
        assert (
            db.fetchval(
                "SELECT count(*) FROM team_user_relation WHERE team_id = $1"
                " AND user_id = $2",
                seeded["team"],
                seeded["friend"],
            )
            == 1
        )


def test_the_pending_applications_are_closed_the_token_is_gone_and_the_ad_withdrawn():
    with database_at(BEFORE) as db:
        seeded = _seed(db)

        db.upgrade(AFTER)

        statuses = {
            row["type"]: row["status"]
            for row in db.fetch(
                "SELECT type, status FROM team_membership_application"
                " WHERE team_id = $1",
                seeded["team"],
            )
        }
        assert statuses == {"INVITATION": "CANCELED", "REQUEST": "REJECTED"}
        assert (
            db.fetchval("SELECT join_token FROM team WHERE id = $1", seeded["team"])
            is None
        )
        # The plaza lists posts by status alone, so an OPEN one on this team
        # would keep offering it after everything else here stopped.
        assert (
            db.fetchval(
                "SELECT status FROM team_recruitment_post WHERE team_id = $1",
                seeded["team"],
            )
            == "CLOSED"
        )


def test_a_new_live_member_row_is_refused_by_the_database():
    """The deliberate violation: written straight into the table, with no
    service in the way — and the database itself says no."""
    with database_at(BEFORE) as db:
        seeded = _seed(db)
        db.upgrade(AFTER)

        async def add_the_friend_back(conn: asyncpg.Connection) -> None:
            await conn.execute(
                "INSERT INTO team_user_relation (id, team_id, user_id, role,"
                " created_at, updated_at)"
                " VALUES (nextval('team_user_relation_seq'), $1, $2, 2, now(), now())",
                seeded["team"],
                seeded["friend"],
            )

        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            db.run(add_the_friend_back)

        assert _live_members(db, seeded["team"]) == [(seeded["owner"], 0)]


def test_a_shared_team_still_takes_members():
    """The rule is about personal teams, not teams: the same write on a shared
    one is what the product does all day."""
    with database_at(BEFORE) as db:
        other = db.fetchval(
            "INSERT INTO team (id, name, handle, intro, description, avatar_id,"
            " created_at, updated_at)"
            " VALUES (nextval('team_seq'), 'Shared', $1, '', '', 0, now(), now())"
            " RETURNING id",
            f"t-{uuid.uuid4().hex[:10]}",
        )
        seeded = _seed(db)
        db.upgrade(AFTER)

        async def join(conn: asyncpg.Connection) -> None:
            await conn.execute(
                "INSERT INTO team_user_relation (id, team_id, user_id, role,"
                " created_at, updated_at)"
                " VALUES (nextval('team_user_relation_seq'), $1, $2, 2, now(), now())",
                other,
                seeded["friend"],
            )

        db.run(join)
        assert _live_members(db, other) == [(seeded["friend"], 2)]
