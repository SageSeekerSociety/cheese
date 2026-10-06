"""6eb53fa33cbf closes the historical tasks nobody owns, and nothing else.

A task kept from a room's old work tree, still open and with no owner, is a
conversation nobody may speak in; it is closed. A historical task someone owns,
and every task made since, keeps its status.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import asyncpg

from tests.conftest import _PG_BASE, _admin_recreate_db
from tests.integration.test_task_conversation_migration import _alembic

BEFORE = "b6e2d94a1c37"
AFTER = "6eb53fa33cbf"


async def _seed(conn) -> dict:
    ids = {
        "team": 9_000_003,
        "project": uuid.uuid4(),
        "room": uuid.uuid4(),
        "orphan": uuid.uuid4(),
        "taken": uuid.uuid4(),
        "current": uuid.uuid4(),
        "done": uuid.uuid4(),
    }
    await conn.execute(
        "INSERT INTO team (id, handle, name, intro, description, avatar_id,"
        " created_at, updated_at) VALUES ($1, 'historical-team', 't', '', '', 0,"
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
    for task, status, owner, historical in (
        ("orphan", "open", None, True),
        ("taken", "open", "bob", True),
        ("current", "open", "bob", False),
        ("done", "closed", None, True),
    ):
        await conn.execute(
            "INSERT INTO tasks (id, project_id, room_id, title, status,"
            " owner_handle, historical_delivery, closed_at, created_at, updated_at)"
            " VALUES ($1, $2, $3, 'work', $4::text, $5, $6::json,"
            # With its offset: a bare date is read in the server's time zone, which
            # is UTC in CI and Asia/Shanghai in production and the local test DB.
            " CASE WHEN $4::text = 'closed' THEN timestamptz '2026-09-01 00:00+00' END,"
            " now(), now())",
            ids[task],
            ids["project"],
            ids["room"],
            status,
            owner,
            '{"id": "tree"}' if historical else None,
        )
    return ids


async def _check(conn, ids: dict) -> None:
    rows = {
        row["id"]: row
        for row in await conn.fetch("SELECT id, status, closed_at FROM tasks")
    }
    assert rows[ids["orphan"]]["status"] == "closed"
    assert rows[ids["orphan"]]["closed_at"] is not None
    assert rows[ids["taken"]]["status"] == "open"
    assert rows[ids["current"]]["status"] == "open"
    assert rows[ids["done"]]["status"] == "closed"
    assert rows[ids["done"]]["closed_at"] == datetime(2026, 9, 1, tzinfo=UTC)


def test_only_the_historical_tasks_nobody_owns_are_closed():
    name = "close_historical_tasks_" + uuid.uuid4().hex[:12]
    url = f"{_PG_BASE}/{name}"
    dsn = url.replace("+asyncpg", "")
    asyncio.run(_admin_recreate_db(name))

    async def run(step):
        conn = await asyncpg.connect(dsn)
        try:
            return await step(conn)
        finally:
            await conn.close()

    try:
        _alembic(url, BEFORE)
        ids = asyncio.run(run(_seed))
        _alembic(url, AFTER)
        asyncio.run(run(lambda conn: _check(conn, ids)))
    finally:

        async def drop():
            conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(drop())
