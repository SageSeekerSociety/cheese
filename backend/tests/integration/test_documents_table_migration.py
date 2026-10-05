"""41a261d02e9e on the rows a deployment already holds.

A fresh database has no documents, so `alembic upgrade head` alone proves
nothing about this migration. This test builds a database at the revision
before it, puts the old shapes into it as literals — a room document whose
current version predates the journal, a room with live state and a receipt but
no document block, old thread briefs, a comment anchored to a node, an event
citing the document — then upgrades and checks what a reader of each would
see. It also runs the migration while another connection, as the backend
being replaced does during a deploy, has read `blocks` and then reads
`topics`, and checks the two wait for each other instead of deadlocking.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import _PG_BASE, _admin_recreate_db

BEFORE = "08b4bcff4ad2"
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


def _id() -> uuid.UUID:
    return uuid.uuid4()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _seed(conn) -> dict:
    ids = {
        "team": 9_000_001,
        "project": _id(),
        "room": _id(),
        "stateless": _id(),
        "threads": _id(),
        "doc": _id(),
        "node1": _id(),
        "node2": _id(),
        "comment": _id(),
        "event": _id(),
        "task": _id(),
        "briefed_task": _id(),
        "brief_doc": _id(),
        "briefed_doc": _id(),
        "brief_node": _id(),
        "operation": _id(),
        "stateless_operation": _id(),
    }
    await conn.execute(
        "INSERT INTO team (id, handle, name, intro, description, avatar_id,"
        " created_at, updated_at) VALUES ($1, 'migration-team', 't', '', '', 0,"
        " now(), now())",
        ids["team"],
    )
    await conn.execute(
        "INSERT INTO projects (id, name, ai_mode, settings, team_id, created_at,"
        " updated_at) VALUES ($1, 'p', 'auto', '{}', $2, now(), now())",
        ids["project"],
        ids["team"],
    )
    for room in ("room", "stateless", "threads"):
        await conn.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, created_at,"
            " updated_at) VALUES ($1, $2, $3, 'topic', 'active', now(), now())",
            ids[room],
            ids["project"],
            room,
        )
    for task, brief in (("task", ""), ("briefed_task", "卡上原有的简报")):
        await conn.execute(
            "INSERT INTO tasks (id, project_id, room_id, title, status, brief,"
            " created_at, updated_at)"
            " VALUES ($1, $2, $3, 'work', 'open', $4, now(), now())",
            ids[task],
            ids["project"],
            ids["threads"],
            brief,
        )

    async def block(id_, kind, topic, content, *, author="alice", refs=(), **extra):
        fields = {
            "id": id_,
            "project_id": ids["project"],
            "topic_id": ids[topic],
            "kind": kind,
            "author_type": "participant",
            "author": author,
            "content": content,
            "refs": json.dumps([str(ref) for ref in refs]),
            **extra,
        }
        holes = ", ".join(f"${i + 1}" for i in range(len(fields)))
        await conn.execute(
            f"INSERT INTO blocks ({', '.join(fields)}, created_at, updated_at)"
            f" VALUES ({holes}, now(), now())",
            *fields.values(),
        )

    # The room's document at its third version; history has only the first
    # two (it predates the journal for the third).
    await block(ids["doc"], "doc", "room", "第一段\n\n第二段", doc_version=3)
    for version, content in ((1, "第一段"), (2, "第一段\n\n草稿")):
        await conn.execute(
            "INSERT INTO living_doc_versions (id, room_id, document_id, version,"
            " content, content_hash, actor, created_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, 'alice', now())",
            _id(),
            ids["room"],
            ids["doc"],
            version,
            content,
            _hash(content),
        )
    await block(
        ids["node1"],
        "doc_node",
        "room",
        "第一段",
        struct_parent=ids["doc"],
        node_type="paragraph",
        struct_order=0.0,
    )
    await block(
        ids["node2"],
        "doc_node",
        "room",
        "第二段",
        author="bob",
        struct_parent=ids["doc"],
        node_type="paragraph",
        struct_order=1.0,
    )
    await block(ids["comment"], "comment", "room", "这段对吗", reply_to=ids["node1"])
    await block(ids["event"], "event", "room", "编辑了文档", refs=[ids["doc"]])
    await conn.execute(
        "INSERT INTO living_doc_states (room_id, state, suggestions, updated_at)"
        " VALUES ($1, $2, '[]', now()), ($3, $4, '[]', now())",
        ids["room"],
        b"room-state",
        ids["stateless"],
        b"stateless-state",
    )
    for operation, room in (
        ("operation", "room"),
        ("stateless_operation", "stateless"),
    ):
        await conn.execute(
            "INSERT INTO living_doc_operations (id, room_id, actor, action,"
            " operation_id, fingerprint, receipt, created_at)"
            " VALUES ($1, $2, 'alice', 'replace', $3, $4, $5, now())",
            _id(),
            ids[room],
            ids[operation],
            "f" * 64,
            json.dumps({"doc_version": 3}),
        )

    # Old thread briefs: one whose card has no brief, one whose card does.
    await block(ids["brief_doc"], "doc", "threads", "线程简报", task_id=ids["task"])
    await block(
        ids["brief_node"],
        "doc_node",
        "threads",
        "线程简报",
        task_id=ids["task"],
        struct_parent=ids["brief_doc"],
        node_type="paragraph",
        struct_order=0.0,
    )
    await block(
        ids["briefed_doc"], "doc", "threads", "另一份", task_id=ids["briefed_task"]
    )
    await conn.execute(
        "INSERT INTO living_doc_versions (id, room_id, document_id, version,"
        " content, content_hash, actor, created_at)"
        " VALUES ($1, $2, $3, 1, '线程简报', $4, 'alice', now())",
        _id(),
        ids["threads"],
        ids["brief_doc"],
        _hash("线程简报"),
    )
    return ids


async def _check(conn, ids) -> None:
    docs = {
        row["room_id"]: row
        for row in await conn.fetch("SELECT * FROM documents ORDER BY created_at")
    }
    # The room's document keeps its id, text and version.
    doc = docs[ids["room"]]
    assert doc["id"] == ids["doc"]
    assert (doc["content"], doc["version"]) == ("第一段\n\n第二段", 3)
    # A room with state but no document block gets an empty one holding it.
    assert (docs[ids["stateless"]]["content"], docs[ids["stateless"]]["version"]) == (
        "",
        0,
    )
    # Thread briefs are not documents.
    assert set(docs) == {ids["room"], ids["stateless"]}

    versions = await conn.fetch(
        "SELECT version, content, content_hash, actor FROM document_versions"
        " WHERE document_id = $1 ORDER BY version",
        ids["doc"],
    )
    assert [row["version"] for row in versions] == [1, 2, 3]
    assert versions[2]["content"] == "第一段\n\n第二段"
    assert versions[2]["content_hash"] == _hash("第一段\n\n第二段")
    assert versions[2]["actor"] == "alice"
    assert await conn.fetchval("SELECT count(*) FROM document_versions") == 3

    nodes = await conn.fetch(
        "SELECT id, content, author FROM document_nodes"
        " WHERE document_id = $1 ORDER BY position",
        ids["doc"],
    )
    assert [(n["id"], n["content"], n["author"]) for n in nodes] == [
        (ids["node1"], "第一段", "alice"),
        (ids["node2"], "第二段", "bob"),
    ]
    assert await conn.fetchval("SELECT count(*) FROM document_nodes") == 2

    briefs = {
        row["id"]: row["brief"]
        for row in await conn.fetch("SELECT id, brief FROM tasks")
    }
    assert briefs == {ids["task"]: "线程简报", ids["briefed_task"]: "卡上原有的简报"}

    kinds = {row["kind"] for row in await conn.fetch("SELECT kind FROM blocks")}
    assert kinds == {"comment", "event"}
    comment = await conn.fetchrow(
        "SELECT content, reply_to FROM blocks WHERE id = $1", ids["comment"]
    )
    assert (comment["content"], comment["reply_to"]) == ("这段对吗", None)
    refs = await conn.fetchval("SELECT refs FROM blocks WHERE id = $1", ids["event"])
    assert json.loads(refs) == [str(doc["id"])]

    states = {
        row["document_id"]: bytes(row["state"])
        for row in await conn.fetch("SELECT document_id, state FROM document_states")
    }
    assert states == {
        ids["doc"]: b"room-state",
        docs[ids["stateless"]]["id"]: b"stateless-state",
    }
    receipts = {
        row["operation_id"]: (row["document_id"], json.loads(row["receipt"]))
        for row in await conn.fetch("SELECT * FROM document_operations")
    }
    assert receipts == {
        ids["operation"]: (ids["doc"], {"doc_version": 3}),
        ids["stateless_operation"]: (
            docs[ids["stateless"]]["id"],
            {"doc_version": 3},
        ),
    }

    # The journal's guarantees hold on the new tables.
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="immutable"):
        await conn.execute("UPDATE document_versions SET content = 'x'")
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="immutable"):
        await conn.execute("UPDATE document_operations SET receipt = '{}'")
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="receipt"):
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO document_operations (id, document_id, actor, action,"
                " operation_id, fingerprint, created_at)"
                " VALUES ($1, $2, 'alice', 'replace', $3, $4, now())",
                _id(),
                ids["doc"],
                _id(),
                "e" * 64,
            )
    # Deleting a document takes its history with it.
    await conn.execute("DELETE FROM documents WHERE id = $1", ids["doc"])
    assert await conn.fetchval("SELECT count(*) FROM document_versions") == 0


def test_the_migration_keeps_what_every_reader_of_a_document_sees():
    name = "documents_migration_" + uuid.uuid4().hex[:12]
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
    _alembic(url, "41a261d02e9e")

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


def test_a_live_request_waits_for_the_migration_instead_of_deadlocking():
    """The backend being replaced keeps serving while the migration runs. A
    request that read the room's messages and then reads its room holds
    `blocks` while it asks for `topics`; the migration needs both. It must not
    be killed, nor kill the migration."""
    name = "documents_migration_" + uuid.uuid4().hex[:12]
    url = f"{_PG_BASE}/{name}"
    dsn = url.replace("+asyncpg", "")
    asyncio.run(_admin_recreate_db(name))
    _alembic(url, BEFORE)

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
            "41a261d02e9e",
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(3)
        outcome = "read"
        try:
            await asyncio.wait_for(
                live.fetch("SELECT id FROM topics WHERE id = $1", ids["room"]), 30
            )
            await tx.commit()
        except Exception as exc:  # noqa: BLE001 — the outcome is what is checked
            outcome = type(exc).__name__
            await tx.rollback()
        finally:
            await live.close()
        _, err = await migration.communicate()
        return migration.returncode or 0, outcome, err.decode()

    async def drop():
        conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await conn.close()

    try:
        code, outcome, err = asyncio.run(run())
    finally:
        asyncio.run(drop())
    assert outcome == "read"
    assert code == 0, err
