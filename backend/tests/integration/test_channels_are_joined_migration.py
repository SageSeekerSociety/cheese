"""Channels are joined (migration d3a8e51c07f2).

The rules, as stated before the migration was written:

- each channel takes in the people of its project who spoke in it in the last
  30 days, in its main line or a 支线 under it, and everyone an open task there
  is assigned to — nobody outside the project, and no AI teammate;
- 综合 seats nobody by name: everyone in the project is in it, so its person
  rows go and its AI teammates stay;
- a channel has no admins any more: an admin seat becomes a member's;
- a stored `all` level was the old default nobody chose, so it becomes the new
  default; a mute stays a mute;
- a private room keeps exactly the seats it had.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "4562fd5e0eeb"
AFTER = "d3a8e51c07f2"

NOW = datetime.now(UTC)
RECENTLY = NOW - timedelta(days=3)
LONG_AGO = NOW - timedelta(days=100)
AGENT = "cheese-0123456789ab"
PEOPLE = ("olivia", "alice", "bob", "carol", "dave", "erin")


class World:
    """One project: olivia owns it, alice and bob and dave are on its team
    (dave has left the project), carol came in from outside, erin is in some
    other project, and AGENT is its AI teammate."""

    def __init__(self, db: ReplayDatabase) -> None:
        self.db = db
        self.project = uuid.uuid4()
        self.general = uuid.uuid4()
        self.front = uuid.uuid4()
        self.back = uuid.uuid4()
        self.private = uuid.uuid4()

    def seed(self) -> None:
        self.db.run(self._seed)

    async def _seed(self, conn: asyncpg.Connection) -> None:
        team = 9_200_001 + uuid.uuid4().int % 1_000_000
        await conn.execute(
            "INSERT INTO team (id, name, intro, description, avatar_id, created_at,"
            " updated_at, handle) VALUES ($1, 't', '', '', 0, now(), now(), $2)",
            team,
            f"team-{team}",
        )
        for handle in (*PEOPLE, AGENT):
            await conn.execute(
                'INSERT INTO "user" (username, email, created_at, updated_at)'
                " SELECT $1::varchar, $1::varchar || '@example.test', now(), now()"
                ' WHERE NOT EXISTS (SELECT 1 FROM "user"'
                " WHERE lower(username) = lower($1::varchar))",
                handle,
            )
        await conn.execute(
            "INSERT INTO agent_bindings (id, user_id, kind, created_at, updated_at)"
            " SELECT $1, id, 'platform', now(), now() FROM \"user\""
            " WHERE username = $2 ON CONFLICT DO NOTHING",
            uuid.uuid4(),
            AGENT,
        )
        await conn.execute(
            'DELETE FROM agent_bindings b USING "user" u WHERE u.id = b.user_id'
            " AND u.username = ANY($1::varchar[])",
            list(PEOPLE),
        )
        await conn.execute(
            "INSERT INTO projects (id, name, ai_mode, settings, team_id,"
            " owner_handle, created_at, updated_at)"
            " VALUES ($1, 'p', 'auto', '{}', $2, 'olivia', now(), now())",
            self.project,
            team,
        )
        for offset, handle in enumerate(("alice", "bob", "dave")):
            await conn.execute(
                "INSERT INTO team_user_relation (id, team_id, user_id, role,"
                " created_at, updated_at)"
                ' SELECT $1, $2, id, 2, now(), now() FROM "user"'
                " WHERE username = $3 AND deleted_at IS NULL",
                team + offset,
                team,
                handle,
            )
        await conn.execute(
            "INSERT INTO project_members (id, project_id, user_handle, created_at,"
            " updated_at) VALUES ($1, $2, 'carol', now(), now())",
            uuid.uuid4(),
            self.project,
        )
        await conn.execute(
            "INSERT INTO project_member_exclusions (id, project_id, user_handle,"
            " created_at, updated_at) VALUES ($1, $2, 'dave', now(), now())",
            uuid.uuid4(),
            self.project,
        )

        await self._room(conn, self.general, "综合", kind="root")
        await self._room(conn, self.front, "前端")
        await self._room(conn, self.back, "后端")
        await self._room(conn, self.private, "私聊", private=True)

        for handle, role in (
            ("olivia", "owner"),
            ("alice", "member"),
            (AGENT, "member"),
        ):
            await self._seat(conn, self.general, handle, role)
        await self._seat(conn, self.front, "olivia", "owner")
        await self._seat(conn, self.front, "bob", "admin")
        await self._seat(conn, self.front, AGENT, "member")
        await self._seat(conn, self.private, "alice", "owner")
        await self._seat(conn, self.private, "bob", "member")

        said = await self._say(conn, self.front, "alice", RECENTLY)
        for author in ("erin", AGENT, "dave"):
            await self._say(conn, self.front, author, RECENTLY)
        await self._say(conn, self.back, "bob", LONG_AGO)
        await self._say(conn, self.private, "carol", RECENTLY)

        thread = uuid.uuid4()
        await conn.execute(
            "INSERT INTO threads (id, project_id, room_id, root_block_id,"
            " created_by, created_at) VALUES ($1, $2, $3, $4, 'alice', now())",
            thread,
            self.project,
            self.front,
            said,
        )
        await self._say(conn, thread, "carol", RECENTLY)

        await conn.execute(
            "INSERT INTO tasks (id, project_id, room_id, title, status, owner_handle,"
            " contributor_handles, created_at, updated_at)"
            " VALUES ($1, $2, $3, 'open one', 'open', 'alice', '[\"carol\"]',"
            " now(), now()),"
            " ($4, $2, $3, 'done one', 'closed', 'olivia', '[\"bob\"]', now(), now())",
            uuid.uuid4(),
            self.project,
            self.back,
            uuid.uuid4(),
        )

        for room, handle, level in (
            (self.front, "alice", "all"),
            (self.front, "olivia", "mute"),
        ):
            await conn.execute(
                "INSERT INTO topic_read_states (id, topic_id, user_handle,"
                " last_read_at, notify_level, created_at, updated_at)"
                " VALUES ($1, $2, $3, now(), $4, now(), now())",
                uuid.uuid4(),
                room,
                handle,
                level,
            )

    async def _room(self, conn, room, title, *, kind="topic", private=False):
        await conn.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, is_private,"
            " created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, 'active', $5, now(), now())",
            room,
            self.project,
            title,
            kind,
            private,
        )
        if kind == "root":
            await conn.execute(
                "UPDATE projects SET root_topic_id = $1 WHERE id = $2",
                room,
                self.project,
            )

    async def _seat(self, conn, room, handle, role):
        await conn.execute(
            "INSERT INTO topic_memberships (id, topic_id, member_handle, role,"
            " created_at, updated_at) VALUES ($1, $2, $3, $4, now(), now())",
            uuid.uuid4(),
            room,
            handle,
            role,
        )

    async def _say(self, conn, conversation, author, at) -> uuid.UUID:
        block = uuid.uuid4()
        await conn.execute(
            "INSERT INTO blocks (id, project_id, conversation_id, kind,"
            " author_type, author, content, refs, created_at, updated_at)"
            " VALUES ($1, $2, $3, 'message', 'participant', $4, 'hi', '[]', $5, $5)",
            block,
            self.project,
            conversation,
            author,
            at,
        )
        return block

    def seats(self, room: uuid.UUID) -> dict[str, str]:
        rows = self.db.fetch(
            "SELECT member_handle, role FROM topic_memberships WHERE topic_id = $1",
            room,
        )
        return {row["member_handle"]: row["role"] for row in rows}


@pytest.fixture(scope="module")
def migrated() -> Iterator[World]:
    with database_at(BEFORE) as db:
        world = World(db)
        world.seed()
        db.upgrade(AFTER)
        yield world


def test_people_who_spoke_lately_or_hold_open_work_join_the_channel(migrated):
    front = migrated.seats(migrated.front)
    # alice spoke in its main line, carol in a 支线 under it; olivia and bob
    # were already seated, and the AI teammate keeps its seat.
    assert set(front) == {"olivia", "bob", "alice", "carol", AGENT}
    # Someone outside the project, and someone who left it, are not taken in.
    assert "erin" not in front and "dave" not in front

    # The open task's owner and its collaborator join; a closed task's do not,
    # and neither does someone who spoke there long ago.
    assert set(migrated.seats(migrated.back)) == {"alice", "carol"}


def test_general_keeps_only_its_ai_teammates(migrated):
    assert migrated.seats(migrated.general) == {AGENT: "member"}


def test_a_channel_has_no_admins(migrated):
    front = migrated.seats(migrated.front)
    assert front["bob"] == "member"
    assert front["olivia"] == "owner"
    assert "admin" not in front.values()


def test_a_private_room_keeps_its_seats(migrated):
    assert migrated.seats(migrated.private) == {"alice": "owner", "bob": "member"}


def test_the_old_default_level_becomes_the_new_default(migrated):
    rows = migrated.db.fetch(
        "SELECT user_handle, notify_level, muted_until FROM topic_read_states"
        " WHERE topic_id = $1",
        migrated.front,
    )
    levels = {row["user_handle"]: row["notify_level"] for row in rows}
    assert levels == {"alice": "mentions", "olivia": "mute"}
    assert all(row["muted_until"] is None for row in rows)
    assert (
        migrated.db.fetchval(
            "SELECT description FROM topics WHERE id = $1", migrated.front
        )
        is None
    )
