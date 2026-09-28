"""An exhausted forge quota reads as a wait, not as a refusal.

Every call the platform makes for a repo draws on the one hourly quota of the
GitHub App installation, so when the pollers and several deliveries share it,
it can run out. That state clears at the reset; a caller told "读取代码仓库失败
（HTTP 403）, retryable: false" gives up on work that would succeed an hour later.
"""

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_exception_handlers
from app.domain.project import forge


def _app_reading_the_repo(monkeypatch, respond) -> TestClient:
    transport = httpx.MockTransport(respond)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(
        forge,
        "binding_for_project",
        AsyncMock(
            return_value=SimpleNamespace(
                api_url="https://api.github.com", repo="acme/widgets"
            )
        ),
    )
    tokens = SimpleNamespace(installation_token=AsyncMock(return_value=("t", "x")))
    monkeypatch.setattr(forge, "tokens_for_project", AsyncMock(return_value=tokens))

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/read")
    async def read() -> dict:
        await forge.repository_data(uuid.uuid4(), None, "/pulls/1")  # type: ignore[arg-type]
        return {}

    return TestClient(app, raise_server_exceptions=False)


def test_an_exhausted_quota_is_retryable_and_says_when(monkeypatch):
    reset = int(time.time()) + 25 * 60

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(reset),
            },
            json={"message": "API rate limit exceeded for installation ID 1."},
        )

    r = _app_reading_the_repo(monkeypatch, respond).get("/read")

    assert r.status_code == 503
    error = r.json()["error"]
    assert error["retryable"] is True
    assert "额度" in error["message"]
    assert "25 分钟" in error["message"] or "26 分钟" in error["message"]


def test_a_secondary_limit_with_retry_after_is_retryable(monkeypatch):
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"retry-after": "120"},
            json={"message": "You have exceeded a secondary rate limit."},
        )

    r = _app_reading_the_repo(monkeypatch, respond).get("/read")

    assert r.status_code == 503
    assert r.json()["error"]["retryable"] is True
    assert "2 分钟" in r.json()["error"]["message"]


@pytest.mark.parametrize("remaining", ["41", None])
def test_a_permission_refusal_is_not_dressed_up_as_a_wait(monkeypatch, remaining):
    def respond(_request: httpx.Request) -> httpx.Response:
        headers = {} if remaining is None else {"x-ratelimit-remaining": remaining}
        return httpx.Response(
            403, headers=headers, json={"message": "Resource not accessible"}
        )

    r = _app_reading_the_repo(monkeypatch, respond).get("/read")

    assert r.status_code == 503
    error = r.json()["error"]
    assert error["retryable"] is False
    assert "HTTP 403" in error["message"]
