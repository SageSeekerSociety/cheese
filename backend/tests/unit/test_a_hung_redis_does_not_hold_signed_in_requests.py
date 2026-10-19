"""A Redis that accepts connections and never answers must not hold every
signed-in request while the closed-account check waits on it.

The check fails open when Redis cannot be asked (``app.core.deleted_accounts``);
a Redis that hangs is one that cannot be asked, so a bearer request has to get
through in about the same bound the rate check gives Redis, not the client's
five-second socket default, and the log must not grow by a line per request.
"""

import asyncio
import time

import httpx
import pytest
from starlette.responses import PlainTextResponse

from app.common.auth import create_access_token
from app.core import deleted_accounts
from app.core.config import settings


class _HungRedis:
    async def get(self, key: str) -> bytes | None:
        await asyncio.Event().wait()
        return None


class _RefusingRedis:
    async def get(self, key: str) -> bytes | None:
        raise ConnectionError("Connection refused")


async def _ok(scope, receive, send) -> None:
    await PlainTextResponse("ok")(scope, receive, send)


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    monkeypatch.setattr(deleted_accounts, "get_redis_client", lambda: _HungRedis())
    monkeypatch.setattr(settings, "request_limit_redis_timeout_s", 0.1)
    transport = httpx.ASGITransport(app=deleted_accounts.ClosedAccountGuard(_ok))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_a_bearer_request_gets_through_without_waiting_on_redis(guarded):
    token = create_access_token(7, handle="someone")

    started = time.monotonic()
    async with guarded as client:
        responses = await asyncio.wait_for(
            asyncio.gather(
                *(
                    client.get("/", headers={"Authorization": f"Bearer {token}"})
                    for _ in range(5)
                ),
                client.get("/", params={"token": token}),
            ),
            timeout=3,
        )
    elapsed = time.monotonic() - started

    assert [r.status_code for r in responses] == [200] * 6
    assert elapsed < 1.0, elapsed


async def test_requests_redis_cannot_answer_log_once_not_each(
    guarded, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(deleted_accounts, "get_redis_client", lambda: _RefusingRedis())
    warnings: list[str] = []
    monkeypatch.setattr(
        deleted_accounts._log, "warning", lambda event, **_: warnings.append(event)
    )
    token = create_access_token(7, handle="someone")

    async with guarded as client:
        for _ in range(3):
            await asyncio.wait_for(
                client.get("/", headers={"Authorization": f"Bearer {token}"}),
                timeout=3,
            )

    assert len(warnings) <= 1, warnings
