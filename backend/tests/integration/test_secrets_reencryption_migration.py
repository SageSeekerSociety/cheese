"""The DATA_ENCRYPTION_KEY migration carries every stored secret across (#1482).

Before it, secrets were Fernet tokens under REALNAME_ENCRYPTION_KEY or, when
that was unset, a key derived from JWT_SECRET. The fixtures under
``tests/fixtures/fernet/`` were written by that code, for both key sources.
After the migration each value must read back through the application exactly
as it was written; a value already in the new format must be left alone; and a
value that cannot be decrypted must stop the migration without changing
anything.

Runs the real ``alembic upgrade`` against a scratch database one revision
behind, the same way as ``test_account_unique_indexes_migration.py``.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from app.core.config import settings
from app.domain.agent.forgejo_tokens import forge_password, open_forge_token
from app.domain.oauth.services import open_oauth_token, seal_oauth_token
from app.domain.subscription.services import open_subscription_token
from app.domain.user.realname_services import realname_dict
from tests.conftest import (
    _PG_BASE,
    _admin_recreate_db,
)

_REVISION = "e7033a179d9d"
_PREVIOUS = "4d0e7a91c203"
_BACKEND = Path(__file__).resolve().parents[2]
_FIXTURES = _BACKEND / "tests" / "fixtures" / "fernet"
_DATA_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
_PROJECT = uuid.UUID("00000000-0000-4000-8000-000000009301")
_API = "https://forge.invalid/api/v1"
_SUBSCRIPTION = uuid.UUID("00000000-0000-4000-8000-000000009401")


def _alembic(db_name: str, fixture: dict, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": f"{_PG_BASE}/{db_name}",
        "JWT_SECRET": fixture["jwt_secret"],
        "DATA_ENCRYPTION_KEY": _DATA_KEY,
    }
    env.pop("REALNAME_ENCRYPTION_KEY", None)
    if fixture["realname_encryption_key"]:
        env["REALNAME_ENCRYPTION_KEY"] = fixture["realname_encryption_key"]
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        env=env,
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
    db_name = f"cheesex_reenc_{uuid.uuid4().hex[:8]}"
    fixture = _load("derived_from_jwt_secret.json")
    # Built up from empty rather than walked down from the head template: the
    # head is past 4b8e1f6c2a93, which cannot be downgraded.
    asyncio.run(_admin_recreate_db(db_name))
    step = _alembic(db_name, fixture, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


@pytest.fixture
def new_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_encryption_key", _DATA_KEY)


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _dsn(db_name: str) -> str:
    return f"{_PG_BASE.replace('+asyncpg', '')}/{db_name}"


async def _seed(db_name: str, tokens: dict[str, str], already_new: str) -> None:
    conn = await asyncpg.connect(_dsn(db_name))
    try:
        await conn.execute(
            """
            INSERT INTO user_real_name_identities
                (id, user_id, encrypted, real_name, student_id, grade, major,
                 class_name, created_at, updated_at)
            VALUES (9001, 9001, true, $1, $2, $3, $4, $5, now(), now()),
                   (9002, 9002, false, '李四', '2021000002', '2021', 'Math',
                    '二班', now(), now())
            """,
            tokens["张三"],
            tokens["2021000001"],
            tokens["2021"],
            tokens["计算机科学与技术"],
            tokens["一班"],
        )
        await conn.execute(
            """
            INSERT INTO user_o_auth_connection
                (id, user_id, provider_id, provider_user_id, access_token,
                 refresh_token, created_at, updated_at)
            VALUES (9101, 9001, 'github_app', 'gh-9001', $1, $2, now(), now()),
                   (9102, 9002, 'github_app', 'gh-9002', $3, NULL, now(), now())
            """,
            tokens["gho_access_token"],
            tokens["ghr_refresh_token"],
            already_new,
        )
        await conn.execute(
            """
            INSERT INTO projects (id, name, ai_mode, settings, created_at, updated_at)
            VALUES ($1, 'migration', 'off', '{}', now(), now())
            """,
            _PROJECT,
        )
        await conn.execute(
            """
            INSERT INTO project_forges
                (id, project_id, kind, url, api_url, repo, default_branch,
                 account_password)
            VALUES ($1, $2, 'forgejo', 'https://forge.invalid/p.git', $3,
                    'project-bot/project', 'main', $4)
            """,
            uuid.uuid4(),
            _PROJECT,
            _API,
            tokens["forge-account-password"],
        )
        await conn.execute(
            """
            INSERT INTO forge_tokens
                (id, project_id, api_url, username, value, expires_at)
            VALUES ($1, $2, $3, 'project-bot', $4, now() + interval '1 hour')
            """,
            uuid.uuid4(),
            _PROJECT,
            _API,
            tokens["forge-oauth-token"],
        )
        await conn.execute(
            """
            INSERT INTO llm_subscriptions
                (id, provider, label, status, access_token_enc, refresh_token_enc,
                 created_by_handle, created_at, updated_at)
            VALUES ($1, 'openai_codex', '', 'active', $2, $3, 'ops', now(), now())
            """,
            _SUBSCRIPTION,
            tokens["gho_access_token"],
            tokens["ghr_refresh_token"],
        )
    finally:
        await conn.close()


async def _rows(db_name: str) -> dict[str, list]:
    conn = await asyncpg.connect(_dsn(db_name))
    try:
        return {
            "identities": await conn.fetch(
                "SELECT * FROM user_real_name_identities WHERE id >= 9000 ORDER BY id"
            ),
            "connections": await conn.fetch(
                "SELECT * FROM user_o_auth_connection WHERE id >= 9000 ORDER BY id"
            ),
            "forges": await conn.fetch(
                "SELECT * FROM project_forges WHERE project_id = $1", _PROJECT
            ),
            "tokens": await conn.fetch(
                "SELECT * FROM forge_tokens WHERE project_id = $1", _PROJECT
            ),
            "subscriptions": await conn.fetch(
                "SELECT * FROM llm_subscriptions WHERE id = $1", _SUBSCRIPTION
            ),
            "version": await conn.fetchval("SELECT version_num FROM alembic_version"),
        }
    finally:
        await conn.close()


def _open(row, field: str) -> str:
    return open_oauth_token(
        row[field],
        user_id=row["user_id"],
        provider_id=row["provider_id"],
        field=field,
    )


@pytest.mark.parametrize(
    "fixture_name", ["derived_from_jwt_secret.json", "explicit_key.json"]
)
def test_old_fernet_values_read_back_after_the_migration(
    db_before_the_migration, new_key, fixture_name
):
    fixture = _load(fixture_name)
    already_new = seal_oauth_token(
        "already-migrated", user_id=9002, provider_id="github_app", field="access_token"
    )
    asyncio.run(_seed(db_before_the_migration, fixture["tokens"], already_new))

    step = _alembic(db_before_the_migration, fixture, "upgrade", _REVISION)
    assert step.returncode == 0, step.stderr
    rows = asyncio.run(_rows(db_before_the_migration))

    encrypted, plain = rows["identities"]
    assert realname_dict(SimpleNamespace(**dict(encrypted))) == {
        "realName": "张三",
        "studentId": "2021000001",
        "grade": "2021",
        "major": "计算机科学与技术",
        "className": "一班",
    }
    assert realname_dict(SimpleNamespace(**dict(plain)))["realName"] == "李四"

    migrated, untouched = rows["connections"]
    assert _open(migrated, "access_token") == "gho_access_token"
    assert _open(migrated, "refresh_token") == "ghr_refresh_token"
    assert untouched["access_token"] == already_new
    assert _open(untouched, "access_token") == "already-migrated"

    (forge,) = rows["forges"]
    assert forge_password(SimpleNamespace(**dict(forge))) == "forge-account-password"
    (lease,) = rows["tokens"]
    assert open_forge_token(SimpleNamespace(**dict(lease))) == "forge-oauth-token"
    (subscription,) = rows["subscriptions"]
    subscription = SimpleNamespace(**dict(subscription))
    assert open_subscription_token(subscription, "access_token_enc") == (
        "gho_access_token"
    )
    assert open_subscription_token(subscription, "refresh_token_enc") == (
        "ghr_refresh_token"
    )
    assert subscription.id_token_enc is None


def test_an_undecryptable_value_stops_the_migration_unchanged(
    db_before_the_migration, new_key
):
    fixture = _load("derived_from_jwt_secret.json")
    tokens = dict(fixture["tokens"])
    # Written under some other key: neither the old nor the new one opens it.
    foreign = _load("explicit_key.json")["tokens"]
    tokens["gho_access_token"] = foreign["gho_access_token"]
    already_new = seal_oauth_token(
        "already-migrated", user_id=9002, provider_id="github_app", field="access_token"
    )
    asyncio.run(_seed(db_before_the_migration, tokens, already_new))
    before = asyncio.run(_rows(db_before_the_migration))

    step = _alembic(db_before_the_migration, fixture, "upgrade", _REVISION)

    assert step.returncode != 0
    assert "user_o_auth_connection.access_token id=9101" in step.stderr
    after = asyncio.run(_rows(db_before_the_migration))
    assert after == before
    assert after["version"] == _PREVIOUS
