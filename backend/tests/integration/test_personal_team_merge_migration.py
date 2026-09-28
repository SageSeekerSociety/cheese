"""The one-personal-team migration merges a user's extra personal teams.

A deployed database may already hold a user with two or more live personal
teams, left by concurrent first requests before the unique index existed. The
migration keeps the oldest, moves what pointed at the others onto it, and only
then adds the index.

Runs the real ``alembic upgrade`` against a scratch database one revision
behind, because the test databases already carry the index and would refuse
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

from tests.conftest import _PG_BASE, _admin_recreate_db

_REVISION = "872d78113ce9"
_PREVIOUS = "c1a7e4d29b58"
_BACKEND = Path(__file__).resolve().parents[2]

_OWNER = 9001
_BYSTANDER = 9002
_KEPT, _EXTRA, _LATER_EXTRA, _BYSTANDERS = 9101, 9102, 9103, 9201


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
    db_name = f"cheesex_personal_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin_recreate_db(db_name))
    step = _alembic(db_name, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


async def _seed(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"""
            INSERT INTO "user" (id, username, email, created_at, updated_at)
            VALUES ({_OWNER}, 'twin-owner', 'twin-owner@example.com', now(), now()),
                   ({_BYSTANDER}, 'bystander', 'bystander@example.com', now(), now())
            """
        )
        await conn.execute(
            f"""
            INSERT INTO team (id, name, intro, description, avatar_id,
                              personal_owner_user_id, created_at, updated_at)
            VALUES ({_KEPT}, '个人', '', '', 0, {_OWNER}, now(), now()),
                   ({_EXTRA}, '个人', '', '', 0, {_OWNER}, now(), now()),
                   ({_LATER_EXTRA}, '个人', '', '', 0, {_OWNER}, now(), now()),
                   ({_BYSTANDERS}, '个人', '', '', 0, {_BYSTANDER}, now(), now())
            """
        )
        await conn.execute(
            f"""
            INSERT INTO team_user_relation
                (id, team_id, user_id, role, created_at, updated_at)
            VALUES (9301, {_KEPT}, {_OWNER}, 0, now(), now()),
                   (9302, {_EXTRA}, {_OWNER}, 0, now(), now()),
                   (9303, {_LATER_EXTRA}, {_OWNER}, 0, now(), now()),
                   (9304, {_BYSTANDERS}, {_BYSTANDER}, 0, now(), now())
            """
        )
        await conn.execute(
            f"""
            INSERT INTO projects (id, name, ai_mode, settings, team_id,
                                  created_at, updated_at)
            VALUES ('{uuid.uuid4()}', 'made in the extra team', 'collaborative',
                    '{{}}', {_EXTRA}, now(), now())
            """
        )
        await conn.execute(
            f"""
            INSERT INTO device (device_id, name, token, owner_user_id, created_at)
            VALUES ('everywhere', 'a', 'tok-a', {_OWNER}, now()),
                   ('extras-only', 'b', 'tok-b', {_OWNER}, now())
            """
        )
        for device, team in [
            ("everywhere", _KEPT),
            ("everywhere", _EXTRA),
            ("everywhere", _LATER_EXTRA),
            ("extras-only", _EXTRA),
            ("extras-only", _LATER_EXTRA),
        ]:
            await conn.execute(
                "INSERT INTO device_team (id, device_id, team_id, created_at,"
                " updated_at) VALUES ($1, $2, $3, now(), now())",
                uuid.uuid4(),
                device,
                team,
            )
        await conn.execute(
            f"""
            INSERT INTO team_machine_limit (team_id, value)
            VALUES ({_EXTRA}, 2), ({_LATER_EXTRA}, 3)
            """
        )
    finally:
        await conn.close()


def test_upgrade_merges_extra_personal_teams_into_the_oldest(db_before_the_migration):
    dsn = _PG_BASE.replace("+asyncpg", "") + f"/{db_before_the_migration}"
    asyncio.run(_seed(dsn))

    result = _alembic(db_before_the_migration, "upgrade", _REVISION)
    assert result.returncode == 0, result.stderr

    async def read() -> dict:
        conn = await asyncpg.connect(dsn)
        try:
            return {
                "live": await conn.fetch(
                    "SELECT personal_owner_user_id, id FROM team"
                    " WHERE personal_owner_user_id IN ($1, $2)"
                    " AND deleted_at IS NULL ORDER BY id",
                    _OWNER,
                    _BYSTANDER,
                ),
                "projects": await conn.fetch("SELECT team_id FROM projects"),
                "devices": await conn.fetch(
                    "SELECT device_id, team_id FROM device_team"
                    " ORDER BY device_id, team_id"
                ),
                "limits": await conn.fetch(
                    "SELECT team_id, value FROM team_machine_limit"
                ),
                "members": await conn.fetch(
                    "SELECT team_id, user_id FROM team_user_relation"
                    " WHERE deleted_at IS NULL AND user_id IN ($1, $2)"
                    " ORDER BY team_id",
                    _OWNER,
                    _BYSTANDER,
                ),
            }
        finally:
            await conn.close()

    after = asyncio.run(read())
    assert [tuple(r) for r in after["live"]] == [
        (_OWNER, _KEPT),
        (_BYSTANDER, _BYSTANDERS),
    ]
    assert [r["team_id"] for r in after["projects"]] == [_KEPT]
    assert [tuple(r) for r in after["devices"]] == [
        ("everywhere", _KEPT),
        ("extras-only", _KEPT),
    ]
    assert [tuple(r) for r in after["limits"]] == [(_KEPT, 2)]
    assert [tuple(r) for r in after["members"]] == [
        (_KEPT, _OWNER),
        (_BYSTANDERS, _BYSTANDER),
    ]

    async def a_second_personal_team() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                f"""
                INSERT INTO team (id, name, intro, description, avatar_id,
                                  personal_owner_user_id, created_at, updated_at)
                VALUES (9104, '个人', '', '', 0, {_OWNER}, now(), now())
                """
            )
        finally:
            await conn.close()

    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(a_second_personal_team())
