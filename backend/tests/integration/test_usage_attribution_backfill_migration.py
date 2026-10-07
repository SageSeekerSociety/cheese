"""7ca6c79ea15b gives the listed usage rows their conversation, and nothing else.

A listed row still without a conversation gets the one the file names, if that
conversation exists in its project; a row that already has one, and a row the
file does not list, are left alone; running the migration again changes nothing.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg

from tests.conftest import _PG_BASE, _admin_recreate_db
from tests.integration.test_task_conversation_migration import BACKEND, _alembic

BEFORE = "52fee3dd7773"
AFTER = "7ca6c79ea15b"
PAIRS = json.loads(
    (
        Path(BACKEND) / "alembic" / "versions" / f"{AFTER}_usage_attribution.json"
    ).read_text()
)


def _stamp(url: str, target: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "stamp", target],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _distinct_conversations(count: int) -> list[dict]:
    picked: list[dict] = []
    for pair in PAIRS:
        if pair["turn_id"] and all(
            p["conversation_id"] != pair["conversation_id"] for p in picked
        ):
            picked.append(pair)
        if len(picked) == count:
            return picked
    raise AssertionError("the file lists too few conversations")


LISTED, ATTRIBUTED, ORPHANED = _distinct_conversations(3)
ELSEWHERE = uuid.uuid4()
ELSEWHERE_TURN = uuid.uuid4()
UNLISTED = uuid.uuid4()
TEAM = 9_000_007
PROJECT = uuid.uuid4()


async def _seed(conn) -> None:
    await conn.execute(
        "INSERT INTO team (id, handle, name, intro, description, avatar_id,"
        " created_at, updated_at) VALUES ($1, 'usage-backfill', 't', '', '', 0,"
        " now(), now())",
        TEAM,
    )
    await conn.execute(
        "INSERT INTO projects (id, name, ai_mode, settings, team_id, created_at,"
        " updated_at) VALUES ($1, 'p', 'auto', '{}', $2, now(), now())",
        PROJECT,
        TEAM,
    )
    # A room registers itself as a conversation; ORPHANED's room is never made.
    for room in (LISTED["conversation_id"], ATTRIBUTED["conversation_id"], ELSEWHERE):
        await conn.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, created_at,"
            " updated_at) VALUES ($1, $2, '房间', 'topic', 'active', now(), now())",
            uuid.UUID(str(room)),
            PROJECT,
        )
    for row_id, conversation, turn in (
        (LISTED["id"], None, None),
        (ATTRIBUTED["id"], ELSEWHERE, ELSEWHERE_TURN),
        (ORPHANED["id"], None, None),
        (UNLISTED, None, None),
    ):
        await conn.execute(
            "INSERT INTO resource_usage (id, project_id, conversation_id, turn_id,"
            " model, input_tokens, output_tokens, total_tokens, cost_usd, kind,"
            " route, credits, created_at, updated_at) VALUES ($1, $2, $3, $4,"
            " 'claude-opus', 10, 5, 15, 0, 'chat', 'subscription', 0, now(), now())",
            uuid.UUID(str(row_id)),
            PROJECT,
            conversation,
            turn,
        )


async def _rows(conn) -> dict:
    return {
        str(row["id"]): (
            str(row["conversation_id"]) if row["conversation_id"] else None,
            str(row["turn_id"]) if row["turn_id"] else None,
        )
        for row in await conn.fetch(
            "SELECT id, conversation_id, turn_id FROM resource_usage"
        )
    }


def test_only_listed_rows_without_a_conversation_get_the_listed_one():
    name = "usage_backfill_" + uuid.uuid4().hex[:12]
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
        asyncio.run(run(_seed))
        _alembic(url, AFTER)
        rows = asyncio.run(run(_rows))
        assert rows[LISTED["id"]] == (LISTED["conversation_id"], LISTED["turn_id"])
        assert rows[ATTRIBUTED["id"]] == (str(ELSEWHERE), str(ELSEWHERE_TURN))
        assert rows[ORPHANED["id"]] == (None, None)
        assert rows[str(UNLISTED)] == (None, None)

        _stamp(url, BEFORE)
        _alembic(url, AFTER)
        assert asyncio.run(run(_rows)) == rows
    finally:

        async def drop():
            conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(drop())
