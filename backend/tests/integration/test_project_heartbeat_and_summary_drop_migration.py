"""Two unused project columns go (migration c5835976a247), and come back on downgrade.

`projects.last_heartbeat_at` and `projects.summary` lost their last writers
with the heartbeat and the one-pager. The upgrade drops both; the downgrade
brings them back with their old defaults, so a deployment rolled back past it
has the schema the older code maps. Runs the real ``alembic`` against a
scratch database, because the test databases are already at head.
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

_REVISION = "c5835976a247"
_PREVIOUS = "2733a598f271"
_BACKEND = Path(__file__).resolve().parents[2]
_COLUMNS = ("last_heartbeat_at", "summary")


def _alembic(db_name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        env={**os.environ, "DATABASE_URL": f"{_PG_BASE}/{db_name}"},
        capture_output=True,
        text=True,
    )


def _connect(db_name: str):
    return asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + f"/{db_name}")


async def _drop(db_name: str) -> None:
    conn = await _connect("postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _project_columns(db_name: str) -> set[str]:
    conn = await _connect(db_name)
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'projects' AND column_name = ANY($1::text[])",
            list(_COLUMNS),
        )
        return {r["column_name"] for r in rows}
    finally:
        await conn.close()


@pytest.fixture
def db_before_the_migration(_pg_schema):
    db_name = f"cheesex_projcols_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin_recreate_db(db_name))
    step = _alembic(db_name, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


def test_the_columns_go_and_a_downgrade_brings_them_back(db_before_the_migration):
    db = db_before_the_migration
    assert asyncio.run(_project_columns(db)) == set(_COLUMNS)

    up = _alembic(db, "upgrade", _REVISION)
    assert up.returncode == 0, up.stderr
    assert asyncio.run(_project_columns(db)) == set()

    down = _alembic(db, "downgrade", _PREVIOUS)
    assert down.returncode == 0, down.stderr
    assert asyncio.run(_project_columns(db)) == set(_COLUMNS)
