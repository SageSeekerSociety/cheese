"""Per-client request limits, from the outside: what a client is answered."""

import asyncio
import logging
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

from app.core import request_limits
from app.core.config import settings
from app.core.redis import get_redis_client
from app.core.request_limits import RequestLimits
from app.core.sandbox_auth import SANDBOX_TOKEN, mint_scoped_token
from app.main import app
from tests.integration.conftest import session_auth_headers, session_token


@pytest.fixture
def limits(monkeypatch):
    """The shipped numbers, unless a test narrows them."""
    for name in (
        "request_rate_per_s",
        "request_rate_burst",
        "request_concurrency",
        "request_queue_depth",
        "request_queue_timeout_s",
    ):
        monkeypatch.setattr(settings, name, type(settings).model_fields[name].default)
    # The Redis-down warning is said at most once a minute per process, so a
    # test that met an unreachable Redis earlier in this worker would silence
    # the next one's warning. Each test starts with its own minute.
    monkeypatch.setattr(
        request_limits, "_redis_warning", request_limits._RedisWarning()
    )

    def narrow(**values) -> None:
        for name, value in values.items():
            monkeypatch.setattr(settings, f"request_{name}", value)

    return narrow


def _user() -> dict[str, str]:
    return session_auth_headers(f"u-{uuid.uuid4().hex[:10]}")


def _address() -> tuple[str, int]:
    return (f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.7", 4000)


def _client(asgi=app, address: tuple[str, int] | None = None) -> AsyncClient:
    transport = ASGITransport(app=asgi, client=address or _address())
    return AsyncClient(transport=transport, base_url="http://testserver")


class _Slow:
    """An app whose requests run until the test lets them finish."""

    def __init__(self) -> None:
        self.running = 0
        self.entered: list[str] = []
        self.release = asyncio.Event()

        async def slow(request):
            self.running += 1
            self.entered.append(request.query_params.get("n", ""))
            try:
                await self.release.wait()
            finally:
                self.running -= 1
            return JSONResponse({"code": 200, "data": None})

        async def boom(_):
            raise RuntimeError("handler failed")

        async def socket(ws: WebSocket):
            await ws.accept()
            await ws.send_text("hello")
            await ws.close()

        self.app = RequestLimits(
            Starlette(
                routes=[
                    Route("/slow", slow),
                    Route("/boom", boom),
                    WebSocketRoute("/ws", socket),
                ]
            )
        )

    async def until_running(self, count: int) -> None:
        for _ in range(500):
            if self.running == count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{self.running} running, expected {count}")


async def test_a_burst_past_the_quota_is_refused_with_retry_after(limits) -> None:
    limits(rate_per_s=0.2, rate_burst=3)
    headers = _user()
    async with _client() as client:
        allowed = [await client.get("/version", headers=headers) for _ in range(3)]
        refused = await client.get("/version", headers=headers)

    assert [r.status_code for r in allowed] == [200, 200, 200]
    policy = '"rate";q=3;w=15, "concurrency";q=16;qu="concurrent-requests"'
    assert allowed[0].headers["ratelimit-policy"] == policy
    assert allowed[0].headers["ratelimit"] == '"rate";r=2;t=5'
    assert allowed[2].headers["ratelimit"] == '"rate";r=0;t=15'

    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "5"
    assert refused.headers["ratelimit"] == '"rate";r=0;t=5'
    assert refused.headers["ratelimit-policy"] == policy
    body = refused.json()
    assert body["code"] == 429
    assert (
        body["type"] == "https://iana.org/assignments/http-problem-types#quota-exceeded"
    )
    assert body["violated-policies"] == ["rate"]
    assert body["error"]["name"] == "QuotaExceededError"
    assert body["error"]["retryable"] is True
    # The access log and the request id still see it: it went through the app.
    assert refused.headers["x-request-id"]


async def test_the_quota_comes_back_at_the_sustained_rate(limits) -> None:
    limits(rate_per_s=1.0, rate_burst=1)
    headers = _user()
    async with _client() as client:
        assert (await client.get("/version", headers=headers)).status_code == 200
        assert (await client.get("/version", headers=headers)).status_code == 429
        await asyncio.sleep(1.05)
        assert (await client.get("/version", headers=headers)).status_code == 200


async def test_two_users_on_one_address_have_separate_quotas(limits) -> None:
    limits(rate_per_s=0.2, rate_burst=1)
    alice, bob = _user(), _user()
    address = _address()
    async with _client(address=address) as client:
        assert (await client.get("/version", headers=alice)).status_code == 200
        assert (await client.get("/version", headers=alice)).status_code == 429
        assert (await client.get("/version", headers=bob)).status_code == 200


async def test_a_forged_credential_is_counted_by_its_address(
    limits, monkeypatch
) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    limits(rate_per_s=0.2, rate_burst=2)
    forged = session_token("mallory")[:-4] + "AAAA"
    async with _client() as client:
        assert (await client.get("/version")).status_code == 200
        assert (await client.get("/version")).status_code == 200
        for attempt in (
            {"Authorization": f"Bearer {forged}"},
            {"Authorization": f"Bearer {uuid.uuid4().hex}"},
            {"X-Cheese-Token": f"{uuid.uuid4().hex}.{uuid.uuid4().hex}"},
        ):
            assert (await client.get("/version", headers=attempt)).status_code == 429
        # A credential that does verify is its own principal.
        assert (await client.get("/version", headers=_user())).status_code == 200


async def test_each_agent_has_its_own_quota_however_it_presents_it(limits) -> None:
    limits(rate_per_s=0.2, rate_burst=2)
    project = str(uuid.uuid4())
    first = mint_scoped_token(project_id=project, agent_handle="agent-one")
    second = mint_scoped_token(project_id=project, agent_handle="agent-two")
    async with _client() as client:
        # The cheese CLI sends X-Cheese-Token; Claude Code through the LLM
        # proxy sends the same credential as a bearer. One agent, one quota.
        assert (
            await client.get("/version", headers={"X-Cheese-Token": first})
        ).status_code == 200
        assert (
            await client.get("/version", headers={"Authorization": f"Bearer {first}"})
        ).status_code == 200
        assert (
            await client.get("/version", headers={"x-api-key": first})
        ).status_code == 429
        assert (
            await client.get("/version", headers={"X-Cheese-Token": second})
        ).status_code == 200


async def test_the_signing_secret_is_one_principal_of_its_own(limits) -> None:
    limits(rate_per_s=0.2, rate_burst=1)
    address = _address()
    async with _client(address=address) as client:
        secret = {"X-Cheese-Token": SANDBOX_TOKEN}
        assert (await client.get("/version", headers=secret)).status_code == 200
        assert (await client.get("/version", headers=secret)).status_code == 429
        assert (await client.get("/version", headers=_user())).status_code == 200


async def test_health_and_metrics_are_never_limited(limits) -> None:
    limits(rate_per_s=0.2, rate_burst=1)
    headers = _user()
    async with _client() as client:
        for path in (
            "/health",
            "/healthz",
            "/metrics",
            "/health",
            "/healthz",
            "/metrics",
        ):
            response = await client.get(path, headers=headers)
            # `/metrics` 是平台管理员那一面，对这个调用者 403 就是它的正常答复。
            # 这里钉的是这串突发没有被限流（不是 429），不是路由自己的判断。
            assert response.status_code in (200, 403), path
            assert "ratelimit" not in response.headers
        assert (await client.get("/version", headers=headers)).status_code == 200


@pytest.fixture
def _ready(monkeypatch):
    """`/readyz` answers without touching a dependency: only the limit is under test."""
    from app.api.routes import health

    async def _up():
        return {"status": "up"}

    monkeypatch.setattr(health, "_check_database", _up)
    monkeypatch.setattr(health, "_check_redis", _up)


async def test_readiness_from_outside_is_limited_like_any_route(
    limits, monkeypatch, _ready
) -> None:
    """`/readyz` is public. Unlimited for anyone, it was a route a stranger
    could call as fast as they liked; it is limited by the caller's address."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    limits(rate_per_s=0.2, rate_burst=2)
    async with _client() as client:
        answers = [(await client.get("/readyz")).status_code for _ in range(3)]
    assert answers == [200, 200, 429]


async def test_readiness_from_inside_is_never_limited(
    limits, monkeypatch, _ready
) -> None:
    """The container healthcheck asks on loopback; the rollout's curl comes
    through the published port, from a trusted proxy hop with no client in
    front of it. Neither may be refused while the process is busy."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "172.18.0.0/16")
    limits(rate_per_s=0.2, rate_burst=1)
    for address in (("127.0.0.1", 4000), ("172.18.0.1", 4000)):
        async with _client(address=address) as client:
            for _ in range(4):
                response = await client.get("/readyz")
                assert response.status_code == 200, address
                assert "ratelimit" not in response.headers


async def test_requests_pass_while_redis_is_down(limits, monkeypatch, caplog) -> None:
    limits(rate_per_s=0.2, rate_burst=1)
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    get_redis_client.cache_clear()
    headers = _user()
    caplog.set_level(logging.WARNING, logger="app.request_limits")
    async with _client() as client:
        answers = [await client.get("/version", headers=headers) for _ in range(5)]
    assert [r.status_code for r in answers] == [200] * 5
    warnings = [r for r in caplog.records if r.name == "app.request_limits"]
    assert len(warnings) == 1


async def test_requests_past_the_concurrency_limit_wait_and_complete(limits) -> None:
    limits(rate_per_s=1000.0, rate_burst=1000)
    slow = _Slow()
    headers = _user()
    async with _client(slow.app) as client:
        calls = [
            asyncio.create_task(client.get("/slow", headers=headers)) for _ in range(20)
        ]
        await slow.until_running(16)
        await asyncio.sleep(0.5)  # the rest reach the line
        assert slow.running == 16  # four are waiting, not running
        slow.release.set()
        answers = await asyncio.gather(*calls)
    assert [r.status_code for r in answers] == [200] * 20


async def test_a_full_line_is_refused_at_once(limits) -> None:
    limits(rate_per_s=1000.0, rate_burst=1000)
    slow = _Slow()
    headers = _user()
    async with _client(slow.app) as client:
        calls = [
            asyncio.create_task(client.get("/slow", headers=headers)) for _ in range(80)
        ]
        await slow.until_running(16)
        await asyncio.sleep(0.5)  # the rest reach the line
        refused = await client.get("/slow", headers=headers)
        slow.release.set()
        answers = await asyncio.gather(*calls)
    assert [r.status_code for r in answers] == [200] * 80
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "1"
    assert refused.headers["ratelimit"] == '"concurrency";r=0'
    assert refused.json()["violated-policies"] == ["concurrency"]


async def test_a_wait_that_runs_out_is_refused(limits) -> None:
    limits(rate_per_s=1000.0, rate_burst=1000, concurrency=1, queue_timeout_s=0.2)
    slow = _Slow()
    headers = _user()
    async with _client(slow.app) as client:
        holding = asyncio.create_task(client.get("/slow", headers=headers))
        await slow.until_running(1)
        refused = await client.get("/slow", headers=headers)
        slow.release.set()
        assert (await holding).status_code == 200
    assert refused.status_code == 429
    assert refused.json()["violated-policies"] == ["concurrency"]


async def test_waiting_requests_run_in_arrival_order(limits) -> None:
    limits(rate_per_s=1000.0, rate_burst=1000, concurrency=1)
    slow = _Slow()
    headers = _user()
    async with _client(slow.app) as client:
        calls = []
        for n in range(4):
            calls.append(
                asyncio.create_task(client.get(f"/slow?n={n}", headers=headers))
            )
            await asyncio.sleep(0.2)  # this one is in line first
        slow.release.set()
        await asyncio.gather(*calls)
    assert slow.entered == ["0", "1", "2", "3"]


async def test_a_failed_request_gives_its_slot_back(limits) -> None:
    limits(rate_per_s=1000.0, rate_burst=1000, concurrency=1, queue_timeout_s=0.5)
    slow = _Slow()
    headers = _user()
    async with _client(slow.app) as client:
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await client.get("/boom", headers=headers)
        slow.release.set()
        assert (await client.get("/slow", headers=headers)).status_code == 200


async def test_a_client_that_hangs_up_gives_its_slot_back(limits) -> None:
    limits(rate_per_s=1000.0, rate_burst=1000, concurrency=1, queue_timeout_s=0.5)
    slow = _Slow()
    headers = _user()
    async with _client(slow.app) as client:
        gone = asyncio.create_task(client.get("/slow", headers=headers))
        await slow.until_running(1)
        gone.cancel()
        await asyncio.gather(gone, return_exceptions=True)
        slow.release.set()
        assert (await client.get("/slow", headers=headers)).status_code == 200


def test_websockets_are_not_limited(limits) -> None:
    limits(rate_per_s=0.2, rate_burst=1)
    slow = _Slow()
    slow.release.set()
    headers = _user()
    with TestClient(slow.app) as client:
        assert client.get("/slow", headers=headers).status_code == 200
        assert client.get("/slow", headers=headers).status_code == 429
        for _ in range(3):
            with client.websocket_connect("/ws", headers=headers) as ws:
                assert ws.receive_text() == "hello"
