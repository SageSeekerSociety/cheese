"""The login budget holds under a burst of simultaneous requests.

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
from app.domain.user.login_security import MAX_LOGIN_ATTEMPTS


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
    """A fresh name, and its budget gone afterwards: a lockout outlives the
    test that earned it."""
    from redis.asyncio import Redis

    from app.core.config import settings
    from app.domain.user.login_security import (
        LOGIN_ATTEMPTS_PREFIX,
        LOGIN_LOCKOUT_PREFIX,
    )

    name = f"budget-{uuid.uuid4().hex[:12]}"
    yield name
    redis = Redis.from_url(settings.redis_url)
    await redis.delete(
        f"{LOGIN_ATTEMPTS_PREFIX}{name}", f"{LOGIN_LOCKOUT_PREFIX}{name}"
    )
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


async def test_a_burst_of_wrong_passwords_gets_no_more_checks_than_the_budget(
    client, accounts, username
) -> None:
    responses = await asyncio.gather(
        *(
            client.post(
                "/users/auth/login",
                json={"username": username, "password": "wrong-password!"},
            )
            for _ in range(4 * MAX_LOGIN_ATTEMPTS)
        )
    )

    assert accounts.checks == MAX_LOGIN_ATTEMPTS
    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(401) == MAX_LOGIN_ATTEMPTS - 1
    assert statuses.count(403) == 3 * MAX_LOGIN_ATTEMPTS + 1


async def test_wrong_passwords_count_down_then_lock(client, accounts, username) -> None:
    def attempt():
        return client.post(
            "/users/auth/login",
            json={"username": username, "password": "wrong-password!"},
        )

    for left in range(MAX_LOGIN_ATTEMPTS - 1, 0, -1):
        resp = await attempt()
        assert resp.status_code == 401, resp.text
        assert f"{left} attempts remaining" in resp.json()["error"]["message"]

    last = await attempt()
    assert last.status_code == 403, last.text
    assert "too many failed attempts" in last.json()["error"]["message"]

    after = await attempt()
    assert after.status_code == 403, after.text
    assert "Try again in" in after.json()["error"]["message"]
    assert accounts.checks == MAX_LOGIN_ATTEMPTS
