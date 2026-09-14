"""Central executor readiness without database-backed room setup."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.domain.agent import execution
from app.domain.agent.central_provider import CentralChannel


def owner_failure(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://owner/call_executor")
    return httpx.HTTPStatusError(
        "executor request failed",
        request=request,
        response=httpx.Response(status, request=request),
    )


def central_without_database() -> CentralChannel:
    central = object.__new__(CentralChannel)
    central._hub = SimpleNamespace()
    return central


@pytest.mark.anyio
async def test_executor_readiness_retries_transient_owner_http_failure(monkeypatch):
    central = central_without_database()
    unavailable = owner_failure(500)
    call = AsyncMock(side_effect=[unavailable, {"pid": 123}])
    monkeypatch.setattr(execution, "call", call)
    monkeypatch.setattr("app.domain.agent.central_provider.asyncio.sleep", AsyncMock())

    await central._wait_executor(
        uuid.uuid4(), uuid.uuid4(), {"device_id": "executor"}, False
    )

    assert call.await_count == 2


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403, 404, 502])
async def test_executor_readiness_does_not_retry_other_http_failure(
    monkeypatch, status
):
    central = central_without_database()
    failure = owner_failure(status)
    call = AsyncMock(side_effect=failure)
    monkeypatch.setattr(execution, "call", call)

    with pytest.raises(httpx.HTTPStatusError):
        await central._wait_executor(
            uuid.uuid4(), uuid.uuid4(), {"device_id": "executor"}, False
        )
    call.assert_awaited_once()


@pytest.mark.anyio
async def test_executor_readiness_timeout_preserves_owner_http_failure(monkeypatch):
    central = central_without_database()
    unavailable = owner_failure(500)
    call = AsyncMock(side_effect=unavailable)
    sleep = AsyncMock()
    monkeypatch.setattr(execution, "call", call)
    monkeypatch.setattr("app.domain.agent.central_provider.asyncio.sleep", sleep)
    monkeypatch.setattr(
        "app.domain.agent.central_provider.time",
        SimpleNamespace(monotonic=Mock(side_effect=[0, 31])),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await central._wait_executor(
            uuid.uuid4(), uuid.uuid4(), {"device_id": "executor"}, False
        )

    assert raised.value is unavailable
    call.assert_awaited_once()
    sleep.assert_not_awaited()
