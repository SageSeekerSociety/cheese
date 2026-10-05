"""f7985445d2bf on the rows a deployment already holds.

A fresh database has no tasks, so `alembic upgrade head` alone proves nothing
about this migration. This builds a database at the revision before it, puts in
a room with a session and two tasks — one with a brief, one without — upgrades,
and checks what a reader of each finds afterwards:

- every room and task is a registered conversation, and stays registered as
  rooms and tasks come and go;
- the room's session is still the room's;
- a brief is not lost: it is the task's document;
- work that was already under way is not sent back to discussion.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg

from tests.conftest import _PG_BASE, _admin_recreate_db

BEFORE = "6d0ce0a4287b"
AFTER = "f7985445d2bf"
BACKEND = Path(__file__).resolve().parents[2]


def _alembic(url: str, target: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def _seed(conn) -> dict:
    ids = {
        "team": 9_000_002,
        "project": uuid.uuid4(),
        "room": uuid.uuid4(),
        "briefed": uuid.uuid4(),
        "bare": uuid.uuid4(),
    }
    await conn.execute(
        "INSERT INTO team (id, handle, name, intro, description, avatar_id,"
        " created_at, updated_at) VALUES ($1, 'conversation-team', 't', '', '', 0,"
        " now(), now())",
        ids["team"],
    )
    await conn.execute(
        "INSERT INTO projects (id, name, ai_mode, settings, team_id, created_at,"
        " updated_at) VALUES ($1, 'p', 'auto', '{}', $2, now(), now())",
        ids["project"],
        ids["team"],
    )
    await conn.execute(
        "INSERT INTO topics (id, project_id, title, kind, status, created_at,"
        " updated_at) VALUES ($1, $2, '房间', 'topic', 'active', now(), now())",
        ids["room"],
        ids["project"],
    )
    for task, brief, created_by in (
        ("briefed", "把导出做成按月分文件。", "alice"),
        ("bare", "", None),
    ):
        await conn.execute(
            "INSERT INTO tasks (id, project_id, room_id, title, status, brief,"
            " created_by, owner_handle, created_at, updated_at)"
            " VALUES ($1, $2, $3, 'work', 'open', $4, $5, 'bob',"
            " now() - interval '1 day', now())",
            ids[task],
            ids["project"],
            ids["room"],
            brief,
            created_by,
        )
    await conn.execute(
        "INSERT INTO agent_sessions (id, topic_id, agent_handle, harness,"
        " resume_token, created_at, updated_at)"
        " VALUES ($1, $2, 'cheese', 'claude-code', 's-room', now(), now())",
        uuid.uuid4(),
        ids["room"],
    )
    return ids


async def _check(conn, ids: dict) -> None:
    kinds = {
        row["id"]: (row["kind"], row["project_id"])
        for row in await conn.fetch("SELECT id, kind, project_id FROM conversations")
    }
    assert kinds == {
        ids["room"]: ("room", ids["project"]),
        ids["briefed"]: ("task", ids["project"]),
        ids["bare"]: ("task", ids["project"]),
    }

    session = await conn.fetchrow(
        "SELECT conversation_id, topic_id, resume_token FROM agent_sessions"
    )
    assert (session["conversation_id"], session["topic_id"]) == (
        ids["room"],
        ids["room"],
    )
    assert session["resume_token"] == "s-room"

    tasks = {
        row["id"]: row
        for row in await conn.fetch(
            "SELECT id, document_id, created_at, started_at, started_by,"
            " started_doc_version FROM tasks"
        )
    }
    briefed, bare = tasks[ids["briefed"]], tasks[ids["bare"]]
    doc = await conn.fetchrow(
        "SELECT content, version, project_id FROM documents WHERE id = $1",
        briefed["document_id"],
    )
    assert doc["content"] == "把导出做成按月分文件。"
    assert doc["project_id"] == ids["project"]
    history = await conn.fetch(
        "SELECT version, content FROM document_versions WHERE document_id = $1",
        briefed["document_id"],
    )
    assert [(r["version"], r["content"]) for r in history] == [
        (doc["version"], "把导出做成按月分文件。")
    ]
    assert bare["document_id"] is None

    # Already under way: started when it was opened, against what it said then.
    for task in (briefed, bare):
        assert task["started_at"] == task["created_at"]
    assert briefed["started_by"] == "alice"
    assert bare["started_by"] == "bob"
    assert briefed["started_doc_version"] == doc["version"]
    assert bare["started_doc_version"] == 0

    # The registry keeps up with rooms and tasks made and removed afterwards.
    new_room = uuid.uuid4()
    await conn.execute(
        "INSERT INTO topics (id, project_id, title, kind, status, created_at,"
        " updated_at) VALUES ($1, $2, '新房间', 'topic', 'active', now(), now())",
        new_room,
        ids["project"],
    )
    await conn.execute("DELETE FROM tasks WHERE id = $1", ids["bare"])
    registered = {
        row["id"]: row["kind"]
        for row in await conn.fetch("SELECT id, kind FROM conversations")
    }
    assert registered.get(new_room) == "room"
    assert ids["bare"] not in registered


def test_the_migration_keeps_every_room_task_and_session_where_readers_find_it():
    name = "task_conversation_migration_" + uuid.uuid4().hex[:12]
    url = f"{_PG_BASE}/{name}"
    dsn = url.replace("+asyncpg", "")
    asyncio.run(_admin_recreate_db(name))
    _alembic(url, BEFORE)

    async def seed():
        conn = await asyncpg.connect(dsn)
        try:
            return await _seed(conn)
        finally:
            await conn.close()

    ids = asyncio.run(seed())
    _alembic(url, AFTER)

    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            await _check(conn, ids)
        finally:
            await conn.close()

    try:
        asyncio.run(check())
    finally:

        async def drop():
            conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(drop())
