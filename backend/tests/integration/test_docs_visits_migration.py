"""``docs_visits`` builds and unbuilds in one step, and dedupes in the schema.

The visit table is the only thing the migration adds, so the test is short: walk
one revision down, walk it back up, and check what the database is left holding
— the columns, and the unique key that makes "one row per visitor per day" a
property of the table rather than of the insert path. That constraint is the
whole reason the table has a ``visitor_id`` string instead of leaning on
``user_id``: an anonymous visitor has no account to key on, and two requests
racing each other must still leave one row.

The database is a copy of the session's migrated template (see ``_pg_schema``)
rather than a chain built from empty: rebuilding 226 revisions to test one of
them would cost minutes for nothing.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

import asyncpg
import pytest

from tests.conftest import _PG_BASE, _TEMPLATE_DB, _clone_db

_REVISION = "b7f4c1a2d903"
_PREVIOUS = "7e4b9d2c1a60"
_BACKEND = Path(__file__).resolve().parents[2]
_DATA_KEY = "b" * 43 + "="
# asyncpg wants a real `date` for a DATE column; a string is a DataError.
_DAY = date(2026, 9, 1)
_DSN = _PG_BASE.replace("+asyncpg", "")


def _alembic(db_name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        env={
            **os.environ,
            "DATABASE_URL": f"{_PG_BASE}/{db_name}",
            "DATA_ENCRYPTION_KEY": _DATA_KEY,
        },
        capture_output=True,
        text=True,
    )


async def _query(db_name: str, sql: str, *params: object):
    conn = await asyncpg.connect(f"{_DSN}/{db_name}")
    try:
        return await conn.fetch(sql, *params)
    finally:
        await conn.close()


async def _drop(db_name: str) -> None:
    conn = await asyncpg.connect(f"{_DSN}/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await conn.close()


def _columns(db_name: str) -> set[str]:
    rows = asyncio.run(
        _query(
            db_name,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'docs_visits'",
        )
    )
    return {row["column_name"] for row in rows}


def _unique_keys(db_name: str) -> set[str]:
    rows = asyncio.run(
        _query(
            db_name,
            "SELECT conname FROM pg_constraint"
            " WHERE conrelid = 'docs_visits'::regclass AND contype = 'u'",
        )
    )
    return {row["conname"] for row in rows}


@pytest.fixture
def db_at_the_previous_revision(_pg_schema):
    """A copy of the migrated template, walked one step back down.

    The template is at the head, which includes this migration — so the copy has
    to be downgraded first for the upgrade below to be the thing under test. It
    is stamped at this revision first, so the downgrade runs this migration's
    alone: the ones after it need not be reversible.
    """
    db_name = f"cheesex_visits_{uuid.uuid4().hex[:8]}"
    asyncio.run(_clone_db(db_name, _TEMPLATE_DB))
    try:
        step = _alembic(db_name, "stamp", _REVISION)
        assert step.returncode == 0, step.stderr
        step = _alembic(db_name, "downgrade", _PREVIOUS)
        assert step.returncode == 0, step.stderr
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


def test_downgrading_removes_the_table(db_at_the_previous_revision):
    assert _columns(db_at_the_previous_revision) == set()


def test_upgrading_builds_it_with_the_dedupe_key(db_at_the_previous_revision):
    step = _alembic(db_at_the_previous_revision, "upgrade", _REVISION)
    assert step.returncode == 0, step.stderr

    assert _columns(db_at_the_previous_revision) == {
        "id",
        "user_id",
        "visitor_id",
        "day",
        "page",
        "created_at",
    }
    assert _unique_keys(db_at_the_previous_revision) == {"uq_docs_visits_day_visitor"}


def test_two_rows_for_one_visitor_on_one_day_are_refused(
    db_at_the_previous_revision,
):
    """The dedupe is the table's, so it holds even for a caller that skips the
    insert path's ``ON CONFLICT DO NOTHING`` — which is what a second process
    writing between the first one's check and its insert would do."""
    step = _alembic(db_at_the_previous_revision, "upgrade", _REVISION)
    assert step.returncode == 0, step.stderr

    insert = (
        "INSERT INTO docs_visits (id, user_id, visitor_id, day, page)"
        " VALUES ($1, $2, $3, $4, $5)"
    )

    async def _run() -> str:
        conn = await asyncpg.connect(f"{_DSN}/{db_at_the_previous_revision}")
        try:
            await conn.execute(insert, uuid.uuid4(), 7, "u:7", _DAY, "index")
            await conn.execute(insert, uuid.uuid4(), 7, "u:7", _DAY, "quickstart")
        except asyncpg.UniqueViolationError as exc:
            return str(exc)
        finally:
            await conn.close()
        return ""

    message = asyncio.run(_run())
    assert "uq_docs_visits_day_visitor" in message
    assert (
        len(
            asyncio.run(
                _query(db_at_the_previous_revision, "SELECT 1 FROM docs_visits")
            )
        )
        == 1
    )


def test_an_anonymous_visitor_may_leave_with_its_account(
    db_at_the_previous_revision,
):
    """``user_id`` is ``ON DELETE SET NULL`` — deleting an account must not
    delete the visit, or yesterday's visitor count would drop every time
    somebody closed their account."""
    step = _alembic(db_at_the_previous_revision, "upgrade", _REVISION)
    assert step.returncode == 0, step.stderr

    async def _run() -> tuple[int, int]:
        conn = await asyncpg.connect(f"{_DSN}/{db_at_the_previous_revision}")
        try:
            await conn.execute(
                'INSERT INTO "user" (id, username, email, created_at, updated_at)'
                " VALUES (7701, 'visitor', 'visitor@example.invalid', now(), now())"
            )
            await conn.execute(
                "INSERT INTO docs_visits (id, user_id, visitor_id, day, page)"
                " VALUES ($1, 7701, 'u:7701', $2, 'index')",
                uuid.uuid4(),
                _DAY,
            )
            await conn.execute('DELETE FROM "user" WHERE id = 7701')
            row = await conn.fetchrow(
                "SELECT user_id IS NULL AS orphaned FROM docs_visits"
            )
            return int(row["orphaned"]), await conn.fetchval(
                "SELECT count(*) FROM docs_visits"
            )
        finally:
            await conn.close()

    orphaned, rows = asyncio.run(_run())
    assert (orphaned, rows) == (1, 1)
