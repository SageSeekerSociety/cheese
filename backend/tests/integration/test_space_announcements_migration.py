"""Announcements written as the old JSON list survive becoming rows.

Every announcement a space already shows must still be there after the move,
saying the same thing, pinned the same way, dated the same, and credited to the
manager who published it. One whose publisher no manager of that space answers
to is kept without an author rather than credited to someone else.

Runs the real ``alembic upgrade`` against a scratch database one revision
behind, because the test databases no longer have the column.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import _PG_BASE, _admin_recreate_db

_REVISION = "4c9f3a81b42d"
_PREVIOUS = "30ee9b5002a9"
_BACKEND = Path(__file__).resolve().parents[2]

_SPACE = 9501
_OWNER, _ADMIN, _MEMBER = 9511, 9512, 9513

_CREATED = 1_759_000_000_000
_EDITED = 1_759_100_000_000


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
    db_name = f"cheesex_announce_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin_recreate_db(db_name))
    step = _alembic(db_name, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


_OLD_LIST = [
    {
        "title": "期中报告改为统一提交 PDF",
        "content": "<p>截止时间不变</p>",
        "publisher": "林夏",
        "createdAt": _CREATED,
        "updatedAt": _EDITED,
        "pinned": True,
    },
    {
        "title": "第 2 章的题目已经全部发布",
        "content": "<p>一共四道</p>",
        "publisher": "zhouran",
        "createdAt": _CREATED,
        "updatedAt": _CREATED,
    },
    {
        "title": "没人认领的公告",
        "content": "",
        "publisher": "早已离开的助教",
        "createdAt": _CREATED,
    },
    "a bare string, not an announcement",
]


async def _seed(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"""
            INSERT INTO "user" (id, username, email, created_at, updated_at)
            VALUES ({_OWNER}, 'linxia', 'linxia@example.com', now(), now()),
                   ({_ADMIN}, 'zhouran', 'zhouran@example.com', now(), now()),
                   ({_MEMBER}, 'member', 'member@example.com', now(), now())
            """
        )
        await conn.execute(
            f"""
            INSERT INTO user_profile
                (user_id, nickname, intro, avatar_id, created_at, updated_at)
            VALUES ({_OWNER}, '林夏', '', 1, now(), now()),
                   ({_ADMIN}, '周然', '', 1, now(), now()),
                   ({_MEMBER}, '早已离开的助教', '', 1, now(), now())
            """
        )
        await conn.execute(
            f"""
            INSERT INTO space (id, name, intro, description, enable_rank,
                               announcements, task_templates,
                               created_at, updated_at)
            VALUES ({_SPACE}, '数据分析课', '', '', false, $1::jsonb, '[]',
                    now(), now())
            """,
            json.dumps(_OLD_LIST),
        )
        await conn.execute(
            f"""
            INSERT INTO space_admin_relation
                (id, space_id, user_id, role, created_at, updated_at)
            VALUES (9521, {_SPACE}, {_OWNER}, 0, now(), now()),
                   (9522, {_SPACE}, {_ADMIN}, 1, now(), now())
            """
        )
    finally:
        await conn.close()


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def test_every_old_announcement_becomes_a_row(db_before_the_migration):
    dsn = _PG_BASE.replace("+asyncpg", "") + f"/{db_before_the_migration}"
    asyncio.run(_seed(dsn))

    result = _alembic(db_before_the_migration, "upgrade", _REVISION)
    assert result.returncode == 0, result.stderr

    async def read() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetch(
                "SELECT space_id, author_id, title, content, pinned, expires_at,"
                " created_at, updated_at FROM space_announcement ORDER BY id"
            )
        finally:
            await conn.close()

    rows = [dict(r) for r in asyncio.run(read())]
    assert rows == [
        {
            "space_id": _SPACE,
            "author_id": _OWNER,
            "title": "期中报告改为统一提交 PDF",
            "content": "<p>截止时间不变</p>",
            "pinned": True,
            "expires_at": None,
            "created_at": _at(_CREATED),
            "updated_at": _at(_EDITED),
        },
        {
            "space_id": _SPACE,
            "author_id": _ADMIN,
            "title": "第 2 章的题目已经全部发布",
            "content": "<p>一共四道</p>",
            "pinned": False,
            "expires_at": None,
            "created_at": _at(_CREATED),
            "updated_at": _at(_CREATED),
        },
        {
            # Its publisher's nickname belongs to a member, not a manager: a
            # member could never have published it, so nobody is credited.
            "space_id": _SPACE,
            "author_id": None,
            "title": "没人认领的公告",
            "content": "",
            "pinned": False,
            "expires_at": None,
            "created_at": _at(_CREATED),
            "updated_at": _at(_CREATED),
        },
    ]
