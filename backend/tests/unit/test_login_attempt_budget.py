"""Wrong passwords for one username slow it down, and hold under a burst.

The routes run for real against a real Redis; only the account store is
replaced, with one whose password check is slow enough that every request in
the burst is in flight before any of them has been answered.
"""

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes import users
from app.core.errors import register_exception_handlers
from app.domain.user import login_security
from app.domain.user.login_security import (
    LOGIN_FIRST_WAIT_SECONDS,
    LOGIN_FREE_FAILURES,
)


class _SlowWrongPassword:
    def __init__(self) -> None:
        self.checks = 0

    async def authenticate(self, *, username: str, password: str):
        self.checks += 1
        await asyncio.sleep(0.05)
        return None


@pytest.fixture
def accounts() -> _SlowWrongPassword:
    return _SlowWrongPassword()


@pytest.fixture
async def username():
    """A fresh name, and its failures gone afterwards: a wait outlives the
    test that started it."""
    from redis.asyncio import Redis

    from app.core.config import settings
    from app.domain.user.login_security import (
        LOGIN_FAILURES_PREFIX,
        LOGIN_WAIT_PREFIX,
    )

    name = f"budget-{uuid.uuid4().hex[:12]}"
    yield name
    redis = Redis.from_url(settings.redis_url)
    await redis.delete(f"{LOGIN_FAILURES_PREFIX}{name}", f"{LOGIN_WAIT_PREFIX}{name}")
    await redis.aclose()


@pytest.fixture
async def client(accounts):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(users.router)
    app.dependency_overrides[users.get_user_auth_service] = lambda: accounts
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _attempt(client: httpx.AsyncClient, username: str):
    return client.post(
        "/users/auth/login",
        json={"username": username, "password": "wrong-password!"},
    )


def _wait_of(resp: httpx.Response) -> int | None:
    data = resp.json()["error"].get("data") or {}
    return data.get("retryAfterSeconds")


async def test_a_burst_of_wrong_passwords_gets_no_more_checks_than_the_free_ones(
    client, accounts, username
) -> None:
    responses = await asyncio.gather(
        *(_attempt(client, username) for _ in range(4 * LOGIN_FREE_FAILURES))
    )

    assert accounts.checks == LOGIN_FREE_FAILURES
    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(401) == LOGIN_FREE_FAILURES
    assert statuses.count(403) == 3 * LOGIN_FREE_FAILURES


async def test_the_last_free_failure_starts_a_wait_that_refuses_unchecked(
    client, accounts, username
) -> None:
    for _ in range(LOGIN_FREE_FAILURES - 1):
        resp = await _attempt(client, username)
        assert resp.status_code == 401, resp.text
        assert _wait_of(resp) is None

    last = await _attempt(client, username)
    assert last.status_code == 401, last.text
    assert _wait_of(last) == LOGIN_FIRST_WAIT_SECONDS

    refused = await _attempt(client, username)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["data"]["reason"] == "too_many_attempts"
    assert 0 < _wait_of(refused) <= LOGIN_FIRST_WAIT_SECONDS
    assert accounts.checks == LOGIN_FREE_FAILURES


async def test_the_wait_doubles_with_each_failure_up_to_the_cap(
    client, username, monkeypatch
) -> None:
    monkeypatch.setattr(login_security, "LOGIN_FIRST_WAIT_SECONDS", 1)
    monkeypatch.setattr(login_security, "LOGIN_MAX_WAIT_SECONDS", 2)

    waits: list[int | None] = []
    while len(waits) < LOGIN_FREE_FAILURES + 3:
        resp = await _attempt(client, username)
        if resp.status_code == 403:
            await asyncio.sleep(_wait_of(resp) + 0.1)
            continue
        assert resp.status_code == 401, resp.text
        waits.append(_wait_of(resp))

    assert waits[LOGIN_FREE_FAILURES - 1 :] == [1, 2, 2, 2]


async def test_attempts_refused_during_a_wait_do_not_lengthen_it(
    client, accounts, username, monkeypatch
) -> None:
    monkeypatch.setattr(login_security, "LOGIN_FIRST_WAIT_SECONDS", 1)
    for _ in range(LOGIN_FREE_FAILURES):
        await _attempt(client, username)

    for _ in range(20):
        assert (await _attempt(client, username)).status_code == 403
    await asyncio.sleep(1.1)

    resp = await _attempt(client, username)
    assert resp.status_code == 401, resp.text
    assert _wait_of(resp) == 2
    assert accounts.checks == LOGIN_FREE_FAILURES + 1
