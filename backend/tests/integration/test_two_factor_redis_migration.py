"""2FA set up while it lived in Redis keeps working after the move (#1482).

Seeds Redis the way the previous code wrote it — the TOTP secret in the clear,
backup codes as SHA-256 digests, the "always required" flag as a key — runs the
real ``alembic upgrade`` into Postgres, and then signs in through the service:
the same authenticator and the same backup codes must still work. A deployment
that cannot reach Redis must stop instead of dropping everyone's factor.
"""

import asyncio
import base64
import hashlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pyotp
import pytest
import redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.domain.user.login_security import TOTPService
from tests.conftest import (
    _PG_BASE,
    _TEMPLATE_DB,
    _admin_recreate_db,
    _clone_db,
    _db_exists,
)

_REVISION = "2a88bca6e12e"
_PREVIOUS = "e7033a179d9d"
_BACKEND = Path(__file__).resolve().parents[2]
_DATA_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
_USER = 9501
_GONE = 9599


def _alembic(db_name: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        env={
            **os.environ,
            "DATABASE_URL": f"{_PG_BASE}/{db_name}",
            "DATA_ENCRYPTION_KEY": _DATA_KEY,
            **env,
        },
        capture_output=True,
        text=True,
    )


async def _drop(db_name: str) -> None:
    conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _seed_user(db_name: str) -> None:
    conn = await asyncpg.connect(f"{_PG_BASE.replace('+asyncpg', '')}/{db_name}")
    try:
        await conn.execute(
            'INSERT INTO "user" (id, username, email, created_at, updated_at)'
            " VALUES ($1, 'totp-migrated', 'totp@example.invalid', now(), now())",
            _USER,
        )
    finally:
        await conn.close()


@pytest.fixture
def db_before_the_migration(_pg_schema):
    db_name = f"cheesex_2fa_{uuid.uuid4().hex[:8]}"
    if asyncio.run(_db_exists(_TEMPLATE_DB)):
        asyncio.run(_clone_db(db_name, _TEMPLATE_DB))
        step = _alembic(db_name, "downgrade", _PREVIOUS)
    else:
        asyncio.run(_admin_recreate_db(db_name))
        step = _alembic(db_name, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    asyncio.run(_seed_user(db_name))
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


@pytest.fixture
def old_redis(monkeypatch: pytest.MonkeyPatch):
    """The Redis the previous code wrote to, emptied before and after."""
    monkeypatch.setattr(settings, "data_encryption_key", _DATA_KEY)
    client = redis.Redis.from_url(settings.redis_url)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


def _in(db_name: str, check):
    async def run():
        engine = create_async_engine(f"{_PG_BASE}/{db_name}", poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await check(TOTPService(session))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_a_factor_set_up_in_redis_still_signs_in(db_before_the_migration, old_redis):
    secret = pyotp.random_base32()
    codes = ["a1b2c3d4", "0f0f0f0f"]
    old_redis.set(f"cheese:totp_secret:{_USER}", secret)
    old_redis.sadd(
        f"cheese:totp_backup:{_USER}",
        *[hashlib.sha256(code.encode()).hexdigest() for code in codes],
    )
    old_redis.set(f"cheese:totp_always:{_USER}", b"1")
    # A key for an account that no longer exists is not an error.
    old_redis.set(f"cheese:totp_secret:{_GONE}", pyotp.random_base32())

    step = _alembic(
        db_before_the_migration, "upgrade", _REVISION, REDIS_URL=settings.redis_url
    )
    assert step.returncode == 0, step.stderr
    old_redis.flushdb()

    async def check(totp: TOTPService):
        assert await totp.is_2fa_enabled(_USER) is True
        assert await totp.is_always_required(_USER) is True
        assert await totp.verify_2fa(_USER, pyotp.TOTP(secret).now()) is True
        assert await totp.verify_backup_code(_USER, codes[0]) is True
        assert await totp.verify_backup_code(_USER, codes[0]) is False
        assert await totp.verify_backup_code(_USER, codes[1]) is True
        assert await totp.is_2fa_enabled(_GONE) is False

    _in(db_before_the_migration, check)


def test_a_deployment_that_cannot_reach_redis_stops(db_before_the_migration):
    step = _alembic(
        db_before_the_migration,
        "upgrade",
        _REVISION,
        REDIS_URL="redis://127.0.0.1:1/0",
        ENVIRONMENT="production",
        JWT_SECRET="a-genuinely-random-48-char-secret-value",
        PLATFORM_ADMIN_HANDLES='["ops"]',
    )
    assert step.returncode != 0
    assert "Cannot reach Redis" in step.stderr


def test_a_development_machine_without_redis_migrates(db_before_the_migration):
    step = _alembic(
        db_before_the_migration,
        "upgrade",
        _REVISION,
        REDIS_URL="redis://127.0.0.1:1/0",
        ENVIRONMENT="development",
    )
    assert step.returncode == 0, step.stderr
