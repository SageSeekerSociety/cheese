"""The project has an overview; a channel has no living document (migration
53267de872b8).

The rules, as stated before the migration was written:

- 综合's document becomes the project's overview, with what it said — also in
  an archived project;
- the library does not list the overview;
- an active channel's document that was never written is deleted, one that was
  becomes a document of the project's own under its channel's name;
- an archived room keeps its document where it is: it is an old room still
  waiting to become a task.
"""

import uuid
from collections.abc import Iterator

import asyncpg
import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "d3a8e51c07f2"
AFTER = "53267de872b8"


class World:
    def __init__(self, db: ReplayDatabase) -> None:
        self.db = db
        self.project = uuid.uuid4()
        self.general = uuid.uuid4()
        self.front = uuid.uuid4()
        self.quiet = uuid.uuid4()
        self.old = uuid.uuid4()
        self.docs: dict[str, uuid.UUID] = {}

    def seed(self) -> None:
        self.db.run(self._seed)

    async def _seed(self, conn: asyncpg.Connection) -> None:
        team = 9_300_001 + uuid.uuid4().int % 1_000_000
        await conn.execute(
            "INSERT INTO team (id, name, intro, description, avatar_id, created_at,"
            " updated_at, handle) VALUES ($1, 't', '', '', 0, now(), now(), $2)",
            team,
            f"team-{team}",
        )
        await conn.execute(
            "INSERT INTO projects (id, name, ai_mode, settings, team_id,"
            " owner_handle, created_at, updated_at)"
            " VALUES ($1, 'p', 'auto', '{}', $2, 'olivia', now(), now())",
            self.project,
            team,
        )
        await self._room(conn, self.general, "综合", kind="root")
        await self._room(conn, self.front, "前端")
        await self._room(conn, self.quiet, "空着的")
        await self._room(conn, self.old, "老房间", status="archived")
        await self._doc(conn, "general", self.general, "## 项目是什么\n\n课程助手。")
        await self._doc(conn, "front", self.front, "登录页走新稿。")
        await self._doc(conn, "quiet", self.quiet, "")
        await self._doc(conn, "old", self.old, "老房间的结论。")

    async def _room(self, conn, room, title, *, kind="topic", status="active"):
        await conn.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, is_private,"
            " created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, false, now(), now())",
            room,
            self.project,
            title,
            kind,
            status,
        )
        if kind == "root":
            await conn.execute(
                "UPDATE projects SET root_topic_id = $1 WHERE id = $2",
                room,
                self.project,
            )

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

    def document(self, name):
        return self.db.fetchrow(
            "SELECT room_id, title, content FROM documents WHERE id = $1",
            self.docs[name],
        )


@pytest.fixture(scope="module")
def migrated() -> Iterator[World]:
    with database_at(BEFORE) as db:
        world = World(db)
        world.seed()
        db.upgrade(AFTER)
        yield world


def test_general_document_becomes_the_project_overview(migrated):
    overview = migrated.db.fetchval(
        "SELECT overview_document_id FROM projects WHERE id = $1", migrated.project
    )
    assert overview == migrated.docs["general"]
    doc = migrated.document("general")
    assert doc["room_id"] is None
    assert doc["content"] == "## 项目是什么\n\n课程助手。"


def test_a_written_channel_document_is_kept_under_its_channel_name(migrated):
    doc = migrated.document("front")
    assert doc["room_id"] is None
    assert doc["title"] == "前端"
    assert doc["content"] == "登录页走新稿。"


def test_an_unwritten_channel_document_is_gone(migrated):
    assert migrated.document("quiet") is None


def test_an_archived_room_keeps_its_document(migrated):
    doc = migrated.document("old")
    assert doc["room_id"] == migrated.old
    assert doc["content"] == "老房间的结论。"
