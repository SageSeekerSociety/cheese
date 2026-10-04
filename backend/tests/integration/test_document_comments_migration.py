"""4383bf20b465 on the comments a deployment already holds.

A fresh database has no comments, so `alembic upgrade head` alone proves nothing
about this migration. This test builds a database at the revision before it,
puts the old shapes into it as literals — a thread with a state row and
numbered replies, an early reply that only answered another comment, a comment
written from a thread's card, a comment in a room with no document row, a
reaction on a comment — then upgrades and checks what a reader of each would
see. It also runs the migration while another connection, as the backend
being replaced does during a deploy, has read `blocks` and then writes
`documents`, and checks the two wait for each other instead of deadlocking.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import _PG_BASE, _admin_recreate_db

BEFORE = "be894366c0a5"
AFTER = "4383bf20b465"
BACKEND = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 9, 1, tzinfo=UTC)


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
        key: uuid.uuid4()
        for key in (
            "project",
            "room",
            "bare",
            "doc",
            "task",
            "root",
            "reply1",
            "reply2",
            "early",
            "earlier",
            "from_card",
            "bare_root",
            "message",
            "plain_root",
        )
    }
    ids["team"] = 9_000_002
    await conn.execute(
        "INSERT INTO team (id, handle, name, intro, description, avatar_id,"
        " created_at, updated_at) VALUES ($1, 'comments-team', 't', '', '', 0,"
        " now(), now())",
        ids["team"],
    )
    await conn.execute(
        "INSERT INTO projects (id, name, ai_mode, settings, team_id, created_at,"
        " updated_at) VALUES ($1, 'p', 'auto', '{}', $2, now(), now())",
        ids["project"],
        ids["team"],
    )
    for room in ("room", "bare"):
        await conn.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, created_at,"
            " updated_at) VALUES ($1, $2, $3, 'topic', 'active', now(), now())",
            ids[room],
            ids["project"],
            room,
        )
    await conn.execute(
        "INSERT INTO documents (id, project_id, room_id, kind, content, version,"
        " author, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'doc', '原文', 1, 'alice', now(), now())",
        ids["doc"],
        ids["project"],
        ids["room"],
    )
    await conn.execute(
        "INSERT INTO tasks (id, project_id, room_id, title, status, brief,"
        " created_at, updated_at)"
        " VALUES ($1, $2, $3, 'work', 'open', '', now(), now())",
        ids["task"],
        ids["project"],
        ids["room"],
    )

    async def block(id_, kind, room, content, minute, *, author="alice", **extra):
        fields = {
            "id": id_,
            "project_id": ids["project"],
            "topic_id": ids[room],
            "kind": kind,
            "author_type": "participant",
            "author": author,
            "content": content,
            "refs": json.dumps([]),
            "created_at": T0 + timedelta(minutes=minute),
            "updated_at": T0 + timedelta(minutes=minute),
            **extra,
        }
        holes = ", ".join(f"${i + 1}" for i in range(len(fields)))
        await conn.execute(
            f"INSERT INTO blocks ({', '.join(fields)}) VALUES ({holes})",
            *fields.values(),
        )

    # A resolved thread at revision 4 with two numbered replies, and an early
    # reply that only answered it (and one answering that), never numbered.
    await block(ids["root"], "comment", "room", "这里不对", 0, anchor_quote="原文")
    await block(
        ids["reply1"], "comment", "room", "哪里", 1, author="bob", reply_to=ids["root"]
    )
    await block(ids["reply2"], "comment", "room", "第二句", 2, reply_to=ids["root"])
    await block(
        ids["earlier"],
        "comment",
        "room",
        "早先的回复",
        3,
        author="carol",
        reply_to=ids["root"],
    )
    await block(
        ids["early"], "comment", "room", "接着早先那条", 4, reply_to=ids["earlier"]
    )
    await conn.execute(
        "INSERT INTO doc_comment_threads (comment_id, revision, state, reply_count)"
        " VALUES ($1, 4, 'resolved', 2)",
        ids["root"],
    )
    for seq, reply in ((1, "reply1"), (2, "reply2")):
        await conn.execute(
            "INSERT INTO doc_comment_replies (block_id, comment_id, sequence)"
            " VALUES ($1, $2, $3)",
            ids[reply],
            ids["root"],
            seq,
        )
    # Written from the thread's card; marked in the room's document, listed
    # nowhere, and with no thread state row.
    await block(
        ids["from_card"],
        "comment",
        "room",
        "卡上写的",
        5,
        task_id=ids["task"],
        anchor_quote="原文",
    )
    # A thread whose state was never read; answering a plain message is not a
    # reply.
    await block(ids["message"], "message", "room", "一条消息", 6)
    await block(
        ids["plain_root"], "comment", "room", "整篇的评论", 7, reply_to=ids["message"]
    )
    # A comment in a room that has no document row.
    await block(ids["bare_root"], "comment", "bare", "空文档上的评论", 8)
    await conn.execute(
        "INSERT INTO block_reactions (id, block_id, emoji, author, created_at)"
        " VALUES ($1, $2, '👍', 'bob', now())",
        uuid.uuid4(),
        ids["root"],
    )
    return ids


async def _check(conn, ids) -> None:
    rows = {
        row["id"]: row
        for row in await conn.fetch(
            "SELECT id, document_id, thread_id, sequence, author, content,"
            " anchor_quote, created_at FROM document_comments"
        )
    }
    threads = {
        row["id"]: row
        for row in await conn.fetch(
            "SELECT id, revision, state, reply_count FROM document_threads"
        )
    }
    bare_doc = await conn.fetchval(
        "SELECT id FROM documents WHERE room_id = $1", ids["bare"]
    )

    # Every comment kept its id: the marks in the document still find them.
    assert set(rows) == {
        ids[k]
        for k in (
            "root",
            "reply1",
            "reply2",
            "earlier",
            "early",
            "from_card",
            "plain_root",
            "bare_root",
        )
    }
    # The thread reads as before: its numbered replies in order, then the early
    # ones after them, oldest first; state and revision carried over.
    replies = sorted(
        (r for r in rows.values() if r["thread_id"] == ids["root"]),
        key=lambda r: r["sequence"],
    )
    assert [r["id"] for r in replies] == [
        ids["reply1"],
        ids["reply2"],
        ids["earlier"],
        ids["early"],
    ]
    assert [r["sequence"] for r in replies] == [1, 2, 3, 4]
    assert [r["author"] for r in replies] == ["bob", "alice", "carol", "alice"]
    assert threads[ids["root"]]["state"] == "resolved"
    assert threads[ids["root"]]["revision"] == 4
    assert threads[ids["root"]]["reply_count"] == 4
    root = rows[ids["root"]]
    assert (root["document_id"], root["anchor_quote"], root["content"]) == (
        ids["doc"],
        "原文",
        "这里不对",
    )
    assert root["created_at"] == T0
    # The card's comment and the one answering a message open threads of their
    # own on the room's document, open and unanswered.
    for key in ("from_card", "plain_root"):
        assert rows[ids[key]]["thread_id"] is None
        assert rows[ids[key]]["document_id"] == ids["doc"]
        assert (threads[ids[key]]["state"], threads[ids[key]]["revision"]) == (
            "open",
            1,
        )
    # A room with comments and no document row got an empty one to hold them.
    assert bare_doc is not None
    assert rows[ids["bare_root"]]["document_id"] == bare_doc
    assert (
        await conn.fetchval("SELECT version FROM documents WHERE id = $1", bare_doc)
        == 0
    )
    # The comment blocks are gone, the message stays, and so do the old tables'
    # absence and the column only comments set.
    assert (
        await conn.fetchval("SELECT count(*) FROM blocks WHERE kind = 'comment'") == 0
    )
    assert (
        await conn.fetchval("SELECT count(*) FROM blocks WHERE id = $1", ids["message"])
        == 1
    )
    tables = {
        row["tablename"] for row in await conn.fetch("SELECT tablename FROM pg_tables")
    }
    assert not tables & {"doc_comment_threads", "doc_comment_replies"}
    assert not await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns"
        " WHERE table_name = 'blocks' AND column_name = 'anchor_quote'"
    )


async def _drop(name: str) -> None:
    conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


@pytest.fixture
def database():
    name = "comments_migration_" + uuid.uuid4().hex[:12]
    url = f"{_PG_BASE}/{name}"
    asyncio.run(_admin_recreate_db(name))
    _alembic(url, BEFORE)
    try:
        yield url, url.replace("+asyncpg", "")
    finally:
        asyncio.run(_drop(name))


def test_the_migration_keeps_what_every_reader_of_a_comment_sees(database):
    url, dsn = database

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

    asyncio.run(check())


def test_a_live_request_waits_for_the_migration_instead_of_deadlocking(database):
    """The backend being replaced keeps serving while the migration runs. A
    request that read the room's messages and then makes the room's document
    holds `blocks` while it waits on `documents`; the migration needs both. It
    must not be killed, nor kill the migration."""
    url, dsn = database

    async def run() -> tuple[int, str, str]:
        conn = await asyncpg.connect(dsn)
        try:
            ids = await _seed(conn)
        finally:
            await conn.close()
        live = await asyncpg.connect(dsn)
        tx = live.transaction()
        await tx.start()
        await live.fetch("SELECT id FROM blocks WHERE topic_id = $1", ids["room"])
        migration = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            AFTER,
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(3)
        outcome = "written"
        try:
            await asyncio.wait_for(
                live.execute(
                    "INSERT INTO documents (id, project_id, kind, content, version,"
                    " author, created_at, updated_at)"
                    " VALUES ($1, $2, 'doc', '', 0, 'system', now(), now())",
                    uuid.uuid4(),
                    ids["project"],
                ),
                30,
            )
            await tx.commit()
        except Exception as exc:  # noqa: BLE001 — the outcome is what is checked
            outcome = type(exc).__name__
            await tx.rollback()
        finally:
            await live.close()
        _, err = await migration.communicate()
        return migration.returncode or 0, outcome, err.decode()

    code, outcome, err = asyncio.run(run())
    assert outcome == "written"
    assert code == 0, err
