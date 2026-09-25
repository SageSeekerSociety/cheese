"""No real-name record stays stored as plaintext.

Records from before real-name fields were encrypted hold their text as
written. After the migration each must be encrypted and read back through the
application exactly as it was written; a record that was already encrypted
must be left as it is; and running the migration again must change nothing.

Runs the real ``alembic upgrade`` against a scratch database one revision
behind, the same way as ``test_secrets_reencryption_migration.py``.
"""

import asyncio
import base64
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from app.core.config import settings
from app.domain.user.realname_services import realname_dict, seal_realname_field
from tests.conftest import _PG_BASE, _admin_recreate_db

_REVISION = "853d38c772dd"
_PREVIOUS = "90e599b0ba34"
_BACKEND = Path(__file__).resolve().parents[2]
_DATA_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()

_PLAIN = {
    "real_name": "李四",
    "student_id": "2021000002",
    "grade": "2021",
    "major": "数学",
    "class_name": "",
}
_SEALED = {
    "real_name": "张三",
    "student_id": "2021000001",
    "grade": "2021",
    "major": "计算机科学与技术",
    "class_name": "一班",
}


def _as_read(fields: dict[str, str]) -> dict[str, str]:
    return {
        "realName": fields["real_name"],
        "studentId": fields["student_id"],
        "grade": fields["grade"],
        "major": fields["major"],
        "className": fields["class_name"],
    }


def _alembic(db_name: str, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": f"{_PG_BASE}/{db_name}",
        "DATA_ENCRYPTION_KEY": _DATA_KEY,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )


def _dsn(db_name: str) -> str:
    return f"{_PG_BASE.replace('+asyncpg', '')}/{db_name}"


async def _drop(db_name: str) -> None:
    conn = await asyncpg.connect(_dsn("postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await conn.close()


@pytest.fixture
def db_before_the_migration(_pg_schema):
    db_name = f"cheesex_rnplain_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin_recreate_db(db_name))
    step = _alembic(db_name, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


@pytest.fixture(autouse=True)
def app_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_encryption_key", _DATA_KEY)


async def _seed(db_name: str) -> None:
    sealed = {k: seal_realname_field(9002, k, v) for k, v in _SEALED.items()}
    conn = await asyncpg.connect(_dsn(db_name))
    try:
        await conn.execute(
            """
            INSERT INTO user_real_name_identities
                (id, user_id, encrypted, real_name, student_id, grade, major,
                 class_name, created_at, updated_at)
            VALUES (9001, 9001, false, $1, $2, $3, $4, $5, now(), now()),
                   (9002, 9002, true, $6, $7, $8, $9, $10, now(), now())
            """,
            *_PLAIN.values(),
            *sealed.values(),
        )
    finally:
        await conn.close()


async def _rows(db_name: str) -> list[dict]:
    conn = await asyncpg.connect(_dsn(db_name))
    try:
        rows = await conn.fetch(
            "SELECT * FROM user_real_name_identities WHERE id >= 9000 ORDER BY id"
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


def test_plaintext_records_are_encrypted_and_read_back_unchanged(
    db_before_the_migration,
):
    asyncio.run(_seed(db_before_the_migration))
    before = asyncio.run(_rows(db_before_the_migration))

    step = _alembic(db_before_the_migration, "upgrade", _REVISION)

    assert step.returncode == 0, step.stderr
    plain, sealed = asyncio.run(_rows(db_before_the_migration))
    assert plain["encrypted"] is True
    assert not {plain[k] for k in _PLAIN if _PLAIN[k]} & set(_PLAIN.values())
    assert realname_dict(SimpleNamespace(**plain)) == _as_read(_PLAIN)
    assert sealed == before[1]
    assert realname_dict(SimpleNamespace(**sealed)) == _as_read(_SEALED)


def test_running_it_again_changes_nothing(db_before_the_migration):
    asyncio.run(_seed(db_before_the_migration))
    assert _alembic(db_before_the_migration, "upgrade", _REVISION).returncode == 0
    once = asyncio.run(_rows(db_before_the_migration))

    assert _alembic(db_before_the_migration, "downgrade", _PREVIOUS).returncode == 0
    step = _alembic(db_before_the_migration, "upgrade", _REVISION)

    assert step.returncode == 0, step.stderr
    assert asyncio.run(_rows(db_before_the_migration)) == once
