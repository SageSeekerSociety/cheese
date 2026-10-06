"""The rooms left archived become closed tasks (migration a6715909ab7d).

The rules, as stated before the migration was written:

- an archived room the first conversion left (its machine cleanup had not
  finished) becomes a closed task of 综合 under the same id, closed when it was
  archived, owned by whoever spoke most in it; nothing said in it is lost, and
  its document and its own tasks follow it as in the first conversion;
- what its cleanup still has to remove stays findable: its machine homes, the
  cleanup record and its device pin are all still there;
- such a room nobody spoke in, with nothing on a machine, is deleted;
- a channel made and archived after the conversion stays a channel;
- no channel or private chat keeps a living document: a written one becomes a
  document of the project's own under its name, an empty one goes;
- on a deployment where nothing has been used as a channel yet (no 支线 was
  ever opened), every archived room is one the conversion left.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import asyncpg
import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "b672fdeb358e"
AFTER = "a6715909ab7d"

BEFORE_CONVERSION = datetime(2026, 9, 1, tzinfo=UTC)
AFTER_CONVERSION = datetime(2026, 10, 6, 9, tzinfo=UTC)
ARCHIVED = datetime(2026, 9, 20, tzinfo=UTC)


class World:
    """One project: 综合, rooms the conversion left, and channels made since."""

    def __init__(self, db: ReplayDatabase, *, channels_used: bool) -> None:
        self.db = db
        self.channels_used = channels_used
        self.project = uuid.uuid4()
        self.general = uuid.uuid4()
        self.rooms: dict[str, uuid.UUID] = {}
        self.docs: dict[str, uuid.UUID] = {}

    def seed(self) -> None:
        self.db.run(self._seed)

    async def _seed(self, conn: asyncpg.Connection) -> None:
        team = 9_400_001 + uuid.uuid4().int % 1_000_000
        await conn.execute(
            "INSERT INTO team (id, name, intro, description, avatar_id, created_at,"
            " updated_at, handle) VALUES ($1, 't', '', '', 0, now(), now(), $2)",
            team,
            f"team-{team}",
        )
        for handle in ("alice", "bob", "olivia"):
            await conn.execute(
                'INSERT INTO "user" (username, email, created_at, updated_at)'
                " SELECT $1::varchar, $1::varchar || '@example.test', now(), now()"
                ' WHERE NOT EXISTS (SELECT 1 FROM "user"'
                " WHERE lower(username) = lower($1::varchar))",
                handle,
            )
        await conn.execute(
            "INSERT INTO projects (id, name, ai_mode, settings, team_id,"
            " owner_handle, created_at, updated_at)"
            " VALUES ($1, 'p', 'auto', '{}', $2, 'olivia', now(), now())",
            self.project,
            team,
        )
        await self._room(conn, self.general, "综合", kind="root")
        await conn.execute(
            "UPDATE projects SET root_topic_id = $1 WHERE id = $2",
            self.general,
            self.project,
        )
        r = self.rooms
        for name in ("left", "left_empty", "archived_since", "private"):
            r[name] = uuid.uuid4()
        await self._room(conn, r["left"], "旧事", archived=ARCHIVED)
        await self._room(conn, r["left_empty"], "没人说话", archived=ARCHIVED)
        await self._room(
            conn,
            r["archived_since"],
            "新频道",
            archived=AFTER_CONVERSION,
            created=AFTER_CONVERSION,
        )
        await self._room(conn, r["private"], "私聊", private=True)

        for _ in range(3):
            await self._say(conn, r["left"], "bob")
        await self._say(conn, r["left"], "alice")
        await self._say(conn, r["archived_since"], "alice")
        if self.channels_used:
            root = await self._say(conn, self.general, "alice")
            await conn.execute(
                "INSERT INTO threads (id, project_id, room_id, root_block_id,"
                " created_by, created_at) VALUES ($1, $2, $3, $4, 'alice', now())",
                uuid.uuid4(),
                self.project,
                self.general,
                root,
            )

        await self._doc(conn, "left", r["left"], "旧事的结论。")
        await self._doc(conn, "archived_since", r["archived_since"], "新频道的笔记。")
        await self._doc(conn, "private", r["private"], "")

        self.inner_task = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tasks (id, project_id, room_id, title, status, owner_handle,"
            " created_at, updated_at)"
            " VALUES ($1, $2, $3, '子任务', 'closed', 'bob', now(), now())",
            self.inner_task,
            self.project,
            r["left"],
        )
        # What the cleanup of the room still has to remove.
        self.session = uuid.uuid4()
        self.resource = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO agent_sessions (id, conversation_id, agent_handle,"
            " harness, work_lease, created_at, updated_at)"
            " VALUES ($1, $2, 'cheese', 'claude-code', $3, now(), now())",
            self.session,
            r["left"],
            json.dumps(
                {
                    "kind": "device",
                    "device_id": "box",
                    "resource_id": self.resource,
                    "room_resource_id": str(r["left"]),
                }
            ),
        )
        self.home = uuid.uuid4()
        await conn.execute(
            "INSERT INTO cloud_host_homes (id, project_id, topic_id,"
            " room_resource_id, resource_id, session_id, archive_key,"
            " archive_published, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, 'bucket/key', false, now(), now())",
            self.home,
            self.project,
            r["left"],
            str(r["left"]),
            self.resource,
            self.session,
        )
        self.cleanup = uuid.uuid4()
        await conn.execute(
            "INSERT INTO room_cleanups (id, project_id, topic_id, resource_id,"
            " due_at, state, resources, created_at, updated_at)"
            " VALUES ($1, $2, $3, $3, now(), 'preparing', $4, now(), now())",
            self.cleanup,
            self.project,
            r["left"],
            json.dumps(
                [{"kind": "device", "device_id": "box", "resource_id": self.resource}]
            ),
        )
        await conn.execute(
            "INSERT INTO device (device_id, name, token, owner_user_id, created_at)"
            " SELECT 'box', 'box', 'tok', id, now() FROM \"user\""
            " WHERE username = 'bob'"
        )
        await conn.execute(
            "INSERT INTO device_topic (topic_id, device_id) VALUES ($1, 'box')",
            r["left"],
        )

    async def _room(
        self,
        conn,
        room,
        title,
        *,
        kind="topic",
        archived=None,
        created=BEFORE_CONVERSION,
        private=False,
    ):
        await conn.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, is_private,"
            " archived_at, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)",
            room,
            self.project,
            title,
            kind,
            "archived" if archived else "active",
            private,
            archived,
            created,
        )

    async def _say(self, conn, conversation, author) -> uuid.UUID:
        block = uuid.uuid4()
        await conn.execute(
            "INSERT INTO blocks (id, project_id, conversation_id, kind,"
            " author_type, author, content, refs, created_at, updated_at)"
            " VALUES ($1, $2, $3, 'message', 'participant', $4, 'hi', '[]',"
            " now() - interval '1 day', now())",
            block,
            self.project,
            conversation,
            author,
        )
        return block

    async def _doc(self, conn, name, room, content):
        doc = uuid.uuid4()
        self.docs[name] = doc
        await conn.execute(
            "INSERT INTO documents (id, project_id, room_id, content, version,"
            " author, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, 'alice', now(), now())",
            doc,
            self.project,
            room,
            content,
            1 if content else 0,
        )

    def task(self, name: str):
        return self.db.fetchrow("SELECT * FROM tasks WHERE id = $1", self.rooms[name])

    def is_room(self, name: str) -> bool:
        return bool(
            self.db.fetchval(
                "SELECT count(*) FROM topics WHERE id = $1", self.rooms[name]
            )
        )


def _migrated(*, channels_used: bool) -> Iterator[World]:
    with database_at(BEFORE) as db:
        world = World(db, channels_used=channels_used)
        world.seed()
        db.upgrade(AFTER)
        yield world


@pytest.fixture(scope="module")
def migrated() -> Iterator[World]:
    yield from _migrated(channels_used=True)


@pytest.fixture(scope="module")
def fresh() -> Iterator[World]:
    yield from _migrated(channels_used=False)


def test_a_room_left_archived_becomes_a_closed_task_of_the_general_channel(
    migrated,
):
    world, db = migrated, migrated.db
    task = world.task("left")
    assert task is not None
    assert not world.is_room("left")
    assert (
        db.fetchval("SELECT kind FROM conversations WHERE id = $1", world.rooms["left"])
        == "task"
    )
    assert task["room_id"] == world.general
    assert task["status"] == "closed"
    assert task["closed_at"] == ARCHIVED
    assert task["owner_handle"] == "bob"
    assert task["document_id"] == world.docs["left"]
    assert (
        db.fetchval(
            "SELECT count(*) FROM blocks WHERE conversation_id = $1",
            world.rooms["left"],
        )
        == 4
    )
    assert (
        db.fetchval("SELECT room_id FROM tasks WHERE id = $1", world.inner_task)
        == world.general
    )


def test_what_its_cleanup_still_removes_stays_findable(migrated):
    world, db = migrated, migrated.db
    room = world.rooms["left"]
    home = db.fetchrow(
        "SELECT topic_id, room_resource_id, archive_key FROM cloud_host_homes"
        " WHERE id = $1",
        world.home,
    )
    assert (home["topic_id"], home["room_resource_id"], home["archive_key"]) == (
        room,
        str(room),
        "bucket/key",
    )
    cleanup = db.fetchrow(
        "SELECT topic_id, state, resources FROM room_cleanups WHERE id = $1",
        world.cleanup,
    )
    assert (cleanup["topic_id"], cleanup["state"]) == (room, "preparing")
    assert json.loads(cleanup["resources"])[0]["resource_id"] == world.resource
    assert (
        db.fetchval("SELECT device_id FROM device_topic WHERE topic_id = $1", room)
        == "box"
    )
    lease = json.loads(
        db.fetchval(
            "SELECT work_lease FROM agent_sessions WHERE id = $1", world.session
        )
    )
    assert lease["room_resource_id"] == str(room)


def test_a_room_left_that_nobody_spoke_in_is_deleted(migrated):
    world = migrated
    assert not world.is_room("left_empty")
    assert world.task("left_empty") is None


def test_a_channel_archived_since_the_conversion_stays_a_channel(migrated):
    world = migrated
    assert world.is_room("archived_since")
    assert world.task("archived_since") is None


def test_no_channel_or_private_chat_keeps_a_document(migrated):
    world, db = migrated, migrated.db
    kept = db.fetchrow(
        "SELECT title, content FROM documents WHERE id = $1",
        world.docs["archived_since"],
    )
    assert (kept["title"], kept["content"]) == ("新频道", "新频道的笔记。")
    assert (
        db.fetchval(
            "SELECT count(*) FROM documents WHERE id = $1", world.docs["private"]
        )
        == 0
    )
    assert world.is_room("private")


def test_where_no_channel_was_used_yet_every_archived_room_was_left(fresh):
    world = fresh
    for name in ("left", "archived_since"):
        task = world.task(name)
        assert task is not None, name
        assert task["status"] == "closed"
        assert not world.is_room(name)
    assert world.is_room("private")
