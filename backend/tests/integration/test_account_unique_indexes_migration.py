"""The account unique-index migration refuses a database that holds duplicates.

Deployed databases may already contain duplicate accounts. The migration must
not pick a survivor itself: it stops, names every conflict, and leaves the data
exactly as it was so the rows can be resolved by hand.

Runs the real ``alembic upgrade`` against a scratch database one revision
behind, because the test databases already carry the indexes and would refuse
the duplicate rows this needs.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import (
    _PG_BASE,
    _admin_recreate_db,
)

_REVISION = "514d7c9cb013"
_PREVIOUS = "8c9ea105b7d2"
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


@pytest.fixture
def db_before_the_migration(_pg_schema):
    db_name = f"cheesex_uniq_{uuid.uuid4().hex[:8]}"
    # Built up from empty rather than walked down from the head template: the
    # head is past 4b8e1f6c2a93, which cannot be downgraded.
    asyncio.run(_admin_recreate_db(db_name))
    step = _alembic(db_name, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


async def _seed_duplicates(dsn: str) -> list[tuple]:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO "user" (id, username, email, created_at, updated_at)
            VALUES (9001, 'Twin', 'twin-a@example.com', now(), now()),
                   (9002, 'twin', 'twin-b@example.com', now(), now())
            """
        )
        await conn.execute(
            """
            INSERT INTO user_o_auth_connection
                (id, user_id, provider_id, provider_user_id, created_at, updated_at)
            VALUES (9101, 9001, 'ruc', 'same-uid', now(), now()),
                   (9102, 9002, 'ruc', 'same-uid', now(), now())
            """
        )
        return await _snapshot(conn)
    finally:
        await conn.close()


async def _snapshot(conn) -> list[tuple]:
    users = await conn.fetch(
        'SELECT id, username, email FROM "user" WHERE id >= 9000 ORDER BY id'
    )
    links = await conn.fetch(
        "SELECT id, user_id, provider_id, provider_user_id"
        " FROM user_o_auth_connection WHERE id >= 9000 ORDER BY id"
    )
    return [tuple(r) for r in users] + [tuple(r) for r in links]


async def _state(dsn: str) -> tuple[list[tuple], str, int]:
    conn = await asyncpg.connect(dsn)
    try:
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        indexes = await conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE indexname = ANY($1::text[])",
            [
                "uq_user_username_lower",
                "uq_user_email_lower",
                "uq_user_profile_user_id",
                "uq_user_o_auth_connection_provider",
                "uq_passkey_credential_id",
            ],
        )
        return await _snapshot(conn), version, indexes
    finally:
        await conn.close()


def test_upgrade_stops_on_duplicates_and_changes_nothing(db_before_the_migration):
    dsn = _PG_BASE.replace("+asyncpg", "") + f"/{db_before_the_migration}"
    before = asyncio.run(_seed_duplicates(dsn))

    result = _alembic(db_before_the_migration, "upgrade", _REVISION)

    assert result.returncode != 0
    report = result.stdout + result.stderr
    assert "'twin'" in report and "[9001, 9002]" in report, report
    assert "'ruc:same-uid'" in report and "[9101, 9102]" in report, report
    rows, version, indexes = asyncio.run(_state(dsn))
    assert rows == before
    assert version == _PREVIOUS
    assert indexes == 0
