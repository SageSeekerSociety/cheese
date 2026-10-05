"""Rooms become tasks of the project's general channel (migration fc3eab9b8847).

The rules, as stated before the migration was written (#2422 ③):

- a project keeps one channel, its root room, now called 综合;
- every other shared room somebody spoke in becomes a task in 综合 under the
  same id, owned by whoever spoke most in it lately, with the others who spoke
  lately as collaborators — and nothing said in it is lost;
- a room nobody spoke in is deleted;
- private rooms, and archived rooms whose machine cleanup is unfinished, stay;
- what a room had follows it: its document, its machine, its cards and its
  tasks; a running session keeps its machine and starts again;
- files people pasted still open from old messages, and the rest of a room's
  files are in the project library under the room's name.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "b6fcc6362b79"
AFTER = "fc3eab9b8847"

NOW = datetime.now(UTC)
RECENTLY = NOW - timedelta(days=3)
LONG_AGO = NOW - timedelta(days=100)
AGENT = "cheese-0123456789ab"


class World:
    """One project with every kind of room the migration sorts."""

    def __init__(self, db: ReplayDatabase) -> None:
        self.db = db
        self.project = uuid.uuid4()
        self.general = uuid.uuid4()
        self.rooms: dict[str, uuid.UUID] = {}

    def seed(self) -> None:
        self.db.run(self._seed)

    async def _seed(self, conn: asyncpg.Connection) -> None:
        team = 9_100_001 + uuid.uuid4().int % 1_000_000
        await conn.execute(
            "INSERT INTO team (id, name, intro, description, avatar_id, created_at,"
            " updated_at, handle) VALUES ($1, 't', '', '', 0, now(), now(), $2)",
            team,
            f"team-{team}",
        )
        for handle in ("alice", "bob", "carol", "dave", "olivia", AGENT):
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
            " AND u.username IN ('alice', 'bob', 'carol', 'dave', 'olivia')"
        )
        await conn.execute(
            "INSERT INTO projects (id, name, ai_mode, settings, team_id,"
            " owner_handle, created_at, updated_at)"
            " VALUES ($1, 'p', 'auto', '{}', $2, 'olivia', now(), now())",
            self.project,
            team,
        )
        await self._room(conn, self.general, "p · 项目总览", kind="root")
        await conn.execute(
            "UPDATE projects SET root_topic_id = $1 WHERE id = $2",
            self.general,
            self.project,
        )
        r = self.rooms
        for name in (
            "solo",
            "pair",
            "quiet",
            "archived",
            "cleaning",
            "private",
            "nested",
            "old",
            "unspoken_with_task",
        ):
            r[name] = uuid.uuid4()
        await self._room(conn, r["solo"], "导出报表")
        await self._room(conn, r["pair"], "设备重连")
        await self._room(conn, r["quiet"], "新话题")
        await self._room(conn, r["archived"], "旧事", status="archived")
        await self._room(conn, r["cleaning"], "还在清理", status="archived")
        await self._room(conn, r["private"], "私聊 · alice · bob", private=True)
        await self._room(conn, r["nested"], "子房间", parent=r["solo"])
        await self._room(conn, r["old"], "很久以前")
        await self._room(conn, r["unspoken_with_task"], "只有任务", created_by="dave")

        say = self._say
        for _ in range(3):
            await say(conn, r["solo"], "alice", RECENTLY)
        await say(conn, r["solo"], "bob", LONG_AGO)
        await say(conn, r["solo"], AGENT, RECENTLY)
        for _ in range(5):
            await say(conn, r["pair"], "bob", RECENTLY)
        for _ in range(2):
            await say(conn, r["pair"], "carol", RECENTLY)
        await say(conn, r["quiet"], AGENT, RECENTLY)
        await say(conn, r["archived"], "alice", LONG_AGO)
        await say(conn, r["cleaning"], "alice", LONG_AGO)
        await say(conn, r["private"], "alice", RECENTLY)
        await say(conn, r["nested"], "carol", RECENTLY)
        await say(conn, r["old"], "carol", LONG_AGO)
        await say(conn, r["old"], "carol", LONG_AGO)
        await say(conn, r["old"], "alice", LONG_AGO)

        # The solo room's document, machine, card, task, session and pointers.
        self.doc = uuid.uuid4()
        await conn.execute(
            "INSERT INTO documents (id, project_id, room_id, content, version,"
            " author, created_at, updated_at)"
            " VALUES ($1, $2, $3, '# 目标', 2, 'alice', now(), now())",
            self.doc,
            self.project,
            r["solo"],
        )
        await conn.execute(
            "UPDATE topics SET compute_config = $1 WHERE id = $2",
            json.dumps({"profile": "cloud"}),
            r["solo"],
        )
        self.card = uuid.uuid4()
        await conn.execute(
            "INSERT INTO accept_cards (id, topic_id, reviewer_handle,"
            " routing_reason, status, note, created_at, updated_at)"
            " VALUES ($1, $2, 'alice', '', 'accepted', '', now(), now())",
            self.card,
            r["solo"],
        )
        self.inner_task = uuid.uuid4()
        for task, room in (
            (self.inner_task, r["solo"]),
            (uuid.uuid4(), r["unspoken_with_task"]),
        ):
            await conn.execute(
                "INSERT INTO tasks (id, project_id, room_id, title, status,"
                " created_at, updated_at)"
                " VALUES ($1, $2, $3, '子任务', 'open', now(), now())",
                task,
                self.project,
                room,
            )
        self.session = uuid.uuid4()
        await conn.execute(
            "INSERT INTO agent_sessions (id, conversation_id, agent_handle,"
            " harness, work_lease, runtime_location, created_at, updated_at)"
            " VALUES ($1, $2, $3, 'claude-code', $4, $5, now(), now())",
            self.session,
            r["solo"],
            AGENT,
            json.dumps(
                {
                    "kind": "device",
                    "device_id": "box",
                    "room_resource_id": str(r["solo"]),
                }
            ),
            json.dumps({"device_id": "center", "resource_id": str(r["solo"])}),
        )
        self.turn = uuid.uuid4()
        await conn.execute(
            "INSERT INTO agent_turns (id, conversation_id, continuation_id,"
            " author, content, is_resume, resendable, started_at)"
            " VALUES ($1, $2, $3, 'alice', 'go', false, true, now())",
            self.turn,
            r["solo"],
            uuid.uuid4(),
        )
        self.home = uuid.uuid4()
        await conn.execute(
            "INSERT INTO cloud_host_homes (id, project_id, topic_id,"
            " room_resource_id, resource_id, session_id, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, now(), now())",
            self.home,
            self.project,
            r["solo"],
            str(r["solo"]),
            str(uuid.uuid4()),
            self.session,
        )
        self.upgraded = await say(conn, self.general, "alice", RECENTLY)
        await conn.execute(
            "UPDATE blocks SET upgraded_to_topic_id = $1 WHERE id = $2",
            r["solo"],
            self.upgraded,
        )
        await conn.execute(
            "INSERT INTO topic_read_states (id, topic_id, user_handle,"
            " last_read_at, created_at, updated_at)"
            " VALUES ($1, $2, 'alice', now(), now(), now())",
            uuid.uuid4(),
            r["solo"],
        )
        for handle in ("alice", "bob", AGENT):
            await self._seat(conn, r["solo"], handle)
        # The pair room's pinned device.
        await conn.execute(
            "INSERT INTO device (device_id, name, token, owner_user_id, created_at)"
            " SELECT 'box', 'box', 'tok', id, now() FROM \"user\""
            " WHERE username = 'bob'"
        )
        await conn.execute(
            "INSERT INTO device_topic (topic_id, device_id) VALUES ($1, 'box')",
            r["pair"],
        )
        # Archived rooms: one finished cleaning up, one did not.
        for room, state in ((r["archived"], "complete"), (r["cleaning"], "pending")):
            await conn.execute(
                "INSERT INTO room_cleanups (id, project_id, topic_id, resource_id,"
                " due_at, state, resources, created_at, updated_at)"
                " VALUES ($1, $2, $3, $3, now(), $4, '{}', now(), now())",
                uuid.uuid4(),
                self.project,
                room,
                state,
            )
        await conn.execute(
            "UPDATE topics SET archived_at = $1 WHERE id = $2",
            LONG_AGO,
            r["archived"],
        )

    async def _room(
        self,
        conn,
        room,
        title,
        *,
        kind="topic",
        status="active",
        private=False,
        parent=None,
        created_by=None,
    ):
        await conn.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, is_private,"
            " parent_id, created_by, title_source, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'human', now(), now())",
            room,
            self.project,
            title,
            kind,
            status,
            private,
            parent,
            created_by,
        )

    async def _seat(self, conn, room, handle):
        await conn.execute(
            "INSERT INTO topic_memberships (id, topic_id, member_handle, role,"
            " created_at, updated_at) VALUES ($1, $2, $3, 'member', now(), now())",
            uuid.uuid4(),
            room,
            handle,
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

    def files(self, workspace: Path) -> None:
        def put(room: uuid.UUID, path: str, data: bytes) -> None:
            target = workspace / ".room-files" / str(self.project) / str(room) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        put(self.rooms["solo"], "uploads/0a1b/shot.png", b"png")
        put(self.rooms["solo"], "办公成果/report.md", b"# report")
        put(self.rooms["pair"], ".imported-task-delivery", b"")
        put(self.rooms["pair"], "backend/app.py", b"print()")

    def task(self, name: str) -> dict | None:
        row = self.db.fetchrow("SELECT * FROM tasks WHERE id = $1", self.rooms[name])
        if row is None:
            return None
        task = dict(row)
        for column in ("contributor_handles", "compute_config"):
            if isinstance(task[column], str):
                task[column] = json.loads(task[column])
        return task

    def is_room(self, room: uuid.UUID) -> bool:
        return bool(self.db.fetchval("SELECT count(*) FROM topics WHERE id = $1", room))

    def kind(self, conversation: uuid.UUID) -> str | None:
        return self.db.fetchval(
            "SELECT kind FROM conversations WHERE id = $1", conversation
        )


@pytest.fixture(scope="module")
def migrated(tmp_path_factory) -> Iterator[tuple[World, Path]]:
    """One seeded world, migrated once for every test here to read."""
    workspace = tmp_path_factory.mktemp("workspace")
    with database_at(BEFORE) as db:
        world = World(db)
        world.seed()
        world.files(workspace)
        db.upgrade(AFTER, env={"WORKSPACE_ROOT": str(workspace)})
        yield world, workspace


def test_rooms_people_spoke_in_become_tasks_of_the_general_channel(migrated):
    world, tmp_path = migrated
    db, r = world.db, world.rooms
    assert db.fetchval("SELECT title FROM topics WHERE id = $1", world.general) == (
        "综合"
    )
    for name in ("solo", "pair", "archived", "nested", "old", "unspoken_with_task"):
        task = world.task(name)
        assert task is not None, name
        assert task["room_id"] == world.general
        assert not world.is_room(r[name])
        assert world.kind(r[name]) == "task"
    # The owner is who spoke most lately; the rest who spoke lately help.
    solo, pair = world.task("solo"), world.task("pair")
    assert (solo["owner_handle"], solo["contributor_handles"]) == ("alice", [])
    assert (pair["owner_handle"], pair["contributor_handles"]) == ("bob", ["carol"])
    assert world.task("old")["owner_handle"] == "carol"
    assert world.task("unspoken_with_task")["owner_handle"] == "dave"
    # An active room is open and started; an archived one closed then.
    assert solo["status"] == "open" and solo["started_at"] is not None
    archived = world.task("archived")
    assert archived["status"] == "closed"
    assert archived["closed_at"] == LONG_AGO
    # Nothing said in a converted room is lost or moved.
    assert (
        db.fetchval("SELECT count(*) FROM blocks WHERE conversation_id = $1", r["solo"])
        == 5
    )


def test_empty_private_and_still_cleaning_rooms(migrated):
    world, tmp_path = migrated
    db, r = world.db, world.rooms
    assert not world.is_room(r["quiet"])
    assert world.task("quiet") is None
    assert world.kind(r["quiet"]) is None
    assert (
        db.fetchval(
            "SELECT count(*) FROM blocks WHERE conversation_id = $1", r["quiet"]
        )
        == 0
    )
    for name in ("private", "cleaning"):
        assert world.is_room(r[name]), name
        assert world.kind(r[name]) == "room"
        assert world.task(name) is None


def test_what_a_room_had_follows_it(migrated):
    world, tmp_path = migrated
    db, r = world.db, world.rooms
    solo = world.task("solo")
    assert solo["document_id"] == world.doc
    assert solo["started_doc_version"] == 2
    assert solo["compute_config"] == {"profile": "cloud"}
    assert solo["agent_handle"] == AGENT
    assert world.task("pair")["compute_config"] == {
        "profile": "device",
        "device_id": "box",
    }
    card = db.fetchrow(
        "SELECT topic_id, task_id FROM accept_cards WHERE id = $1", world.card
    )
    assert (card["topic_id"], card["task_id"]) == (world.general, r["solo"])
    assert (
        db.fetchval("SELECT room_id FROM tasks WHERE id = $1", world.inner_task)
        == world.general
    )
    assert (
        db.fetchval(
            "SELECT upgraded_to_task_id FROM blocks WHERE id = $1", world.upgraded
        )
        == r["solo"]
    )
    roster = {
        row["member_handle"]
        for row in db.fetch(
            "SELECT member_handle FROM topic_memberships WHERE topic_id = $1",
            world.general,
        )
    }
    assert {"alice", "bob", AGENT} <= roster


def test_a_running_session_keeps_its_machine_and_starts_again(migrated):
    world, tmp_path = migrated
    db = world.db
    general = str(world.general)
    session = db.fetchrow(
        "SELECT work_lease, runtime_location FROM agent_sessions WHERE id = $1",
        world.session,
    )
    assert session["runtime_location"] is None
    lease = json.loads(session["work_lease"])
    assert (lease["device_id"], lease["room_resource_id"]) == ("box", general)
    assert (
        db.fetchval("SELECT stopped_at FROM agent_turns WHERE id = $1", world.turn)
        is not None
    )
    home = db.fetchrow(
        "SELECT topic_id, room_resource_id, session_id FROM cloud_host_homes"
        " WHERE id = $1",
        world.home,
    )
    assert (home["topic_id"], home["room_resource_id"], home["session_id"]) == (
        world.general,
        general,
        world.session,
    )


def test_pasted_images_still_open_and_the_rest_is_in_the_library(migrated):
    world, tmp_path = migrated
    project = str(world.project)
    general_files = tmp_path / ".room-files" / project / str(world.general)
    assert (general_files / "uploads/0a1b/shot.png").read_bytes() == b"png"
    library = tmp_path / ".library" / project
    assert (library / "导出报表/办公成果/report.md").read_bytes() == b"# report"
    assert (
        world.db.fetchval(
            "SELECT count(*) FROM library_files WHERE project_id = $1 AND name = $2",
            world.project,
            "导出报表/办公成果/report.md",
        )
        == 1
    )
    # A whole old worktree copy is not poured into the library.
    assert not (library / "设备重连").exists()
