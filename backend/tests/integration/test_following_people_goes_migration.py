"""Following people goes (migration 207284261d7f), and comes back on downgrade.

The upgrade drops the follow table and its sequence; the downgrade brings both
back empty, so a deployment rolled back past it has the schema the older code
reads. Runs the real ``alembic`` against a scratch database, because the test
databases are already at head.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import _PG_BASE, _admin_recreate_db

_REVISION = "207284261d7f"
_PREVIOUS = "90e599b0ba34"
_BACKEND = Path(__file__).resolve().parents[2]


def _alembic(db_name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        env={**os.environ, "DATABASE_URL": f"{_PG_BASE}/{db_name}"},
        capture_output=True,
        text=True,
    )


async def _drop(db_name: str) -> None:
    conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _follow_schema(db_name: str) -> tuple[bool, bool]:
    conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + f"/{db_name}")
    try:
        table = await conn.fetchval(
            "SELECT to_regclass('public.user_following_relationship')"
        )
        sequence = await conn.fetchval(
            "SELECT to_regclass('public.user_following_relationship_id_seq')"
        )
        return table is not None, sequence is not None
    finally:
        await conn.close()


@pytest.fixture
def db_before_the_migration(_pg_schema):
    db_name = f"cheesex_follow_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin_recreate_db(db_name))
    step = _alembic(db_name, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


def test_the_follow_table_goes_and_a_downgrade_brings_it_back(db_before_the_migration):
    db = db_before_the_migration
    assert asyncio.run(_follow_schema(db)) == (True, True)

    up = _alembic(db, "upgrade", _REVISION)
    assert up.returncode == 0, up.stderr
    assert asyncio.run(_follow_schema(db)) == (False, False)

    down = _alembic(db, "downgrade", _PREVIOUS)
    assert down.returncode == 0, down.stderr
    assert asyncio.run(_follow_schema(db)) == (True, True)

    again = _alembic(db, "upgrade", _REVISION)
    assert again.returncode == 0, again.stderr
    assert asyncio.run(_follow_schema(db)) == (False, False)
