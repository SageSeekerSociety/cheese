"""Request lock waits have a configurable bound and an HTTP retry signal."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import db


def _database_error(sqlstate: str) -> DBAPIError:
    class PgError(Exception):
        pass

    original = PgError("database error")
    original.sqlstate = sqlstate
    return DBAPIError("SELECT 1", {}, original, False)


@pytest.mark.parametrize("configured", [None, "750ms", "20s"])
def test_request_lock_timeout_reads_configuration(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("DB_REQUEST_LOCK_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("DB_REQUEST_LOCK_TIMEOUT", configured)
    connection = Mock()

    db._set_request_lock_timeout(Mock(), SimpleNamespace(nested=False), connection)

    _, parameters = connection.execute.call_args.args
    assert parameters["value"] == (configured or "5s")


@pytest.fixture
def request_session(monkeypatch):
    session = AsyncSession()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    monkeypatch.setattr(db, "async_session_factory", lambda: session)
    return session


@pytest.mark.parametrize("when", ["query", "commit"])
async def test_lock_not_available_rolls_back_and_returns_retryable_503(
    request_session, when
):
    failure = _database_error("55P03")
    dependency = db.get_db()
    assert await anext(dependency) is request_session
    if when == "commit":
        request_session.commit.side_effect = failure
        operation = anext(dependency)
    else:
        operation = dependency.athrow(failure)

    with pytest.raises(HTTPException) as caught:
        await operation

    assert caught.value.status_code == 503
    assert int(caught.value.headers["Retry-After"]) > 0
    assert caught.value.__cause__ is failure
    request_session.rollback.assert_awaited_once()


@pytest.mark.parametrize("sqlstate", ["23505", "40P01", "57014", None])
async def test_other_errors_are_rolled_back_and_reraised(request_session, sqlstate):
    failure = _database_error(sqlstate) if sqlstate else RuntimeError("route failed")
    dependency = db.get_db()
    await anext(dependency)

    with pytest.raises(type(failure)) as caught:
        await dependency.athrow(failure)

    assert caught.value is failure
    request_session.rollback.assert_awaited_once()


async def test_success_still_commits(request_session):
    dependency = db.get_db()
    await anext(dependency)
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)
    request_session.commit.assert_awaited_once()
    request_session.rollback.assert_not_awaited()
