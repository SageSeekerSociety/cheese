"""`/readyz` is public: how often it is asked must not decide what it costs.

Each probe opens a fresh database connection and a fresh Redis client. Probed
once per request, a few hundred concurrent anonymous requests would hold a few
hundred Postgres connections and starve the app's own pool — the platform down
for everyone. And the rollout's curl gives the answer 3 s, so two dependencies
that hang must still be answered as a 503 inside that.
"""

import asyncio
import time
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from app.api.routes import health
from app.core import db
from app.main import app


def _client() -> AsyncClient:
    # Loopback, as the container healthcheck: the rate limit is not under test.
    transport = ASGITransport(app=app, client=("127.0.0.1", 4000))
    return AsyncClient(transport=transport, base_url="http://testserver")


class _Connection:
    async def execute(self, _statement):
        return None


class _CountingEngine:
    """Stands in for `probe_engine`: counts the connections probes open."""

    def __init__(self, hold_s: float) -> None:
        self.opened = 0
        self.hold_s = hold_s

    @asynccontextmanager
    async def connect(self):
        self.opened += 1
        await asyncio.sleep(self.hold_s)
        yield _Connection()


class _HangingRedis:
    """A Redis that accepted the connection and never answers."""

    @classmethod
    def from_url(cls, *_args, **_kwargs):
        return cls()

    async def ping(self):
        await asyncio.sleep(60)

    async def aclose(self):
        return None


async def test_concurrent_probes_open_one_database_connection(monkeypatch) -> None:
    engine = _CountingEngine(hold_s=0.3)
    monkeypatch.setattr(db, "probe_engine", engine)

    async def _up():
        return {"status": "up"}

    monkeypatch.setattr(health, "_check_redis", _up)

    async with _client() as client:
        answers = await asyncio.gather(*(client.get("/readyz") for _ in range(20)))

    assert [r.status_code for r in answers] == [200] * 20
    assert engine.opened == 1


async def test_an_answer_is_reused_briefly_then_probed_again(monkeypatch) -> None:
    engine = _CountingEngine(hold_s=0)
    monkeypatch.setattr(db, "probe_engine", engine)
    monkeypatch.setattr(health, "PROBE_REUSE_S", 0.2)

    async with _client() as client:
        await client.get("/readyz")
        await client.get("/readyz")
        assert engine.opened == 1
        await asyncio.sleep(0.25)
        await client.get("/readyz")

    assert engine.opened == 2


async def test_two_hung_dependencies_are_a_503_within_the_rollout_wait(
    monkeypatch,
) -> None:
    monkeypatch.setattr(db, "probe_engine", _CountingEngine(hold_s=60))
    monkeypatch.setattr(health, "AsyncRedis", _HangingRedis)

    started = time.monotonic()
    async with _client() as client:
        # Bounded here too, so a regression fails in seconds instead of hanging.
        response = await asyncio.wait_for(client.get("/readyz"), 10)
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert set(response.json()["unready"]) == {"database", "redis"}
    assert elapsed < 3.0


async def test_a_caller_that_hangs_up_does_not_cancel_the_shared_probe(
    monkeypatch,
) -> None:
    engine = _CountingEngine(hold_s=0.3)
    monkeypatch.setattr(db, "probe_engine", engine)

    first = asyncio.create_task(health.dependency_probe())
    await asyncio.sleep(0.05)
    second = asyncio.create_task(health.dependency_probe())
    await asyncio.sleep(0.05)
    first.cancel()

    database, _redis = await second
    assert database["status"] == "up"
    assert engine.opened == 1
