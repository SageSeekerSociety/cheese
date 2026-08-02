import json

import pytest

from app.core import taskiq_health
from app.core.taskiq_health import heartbeat_is_fresh, make_heartbeat


def test_heartbeat_requires_current_release_and_recent_worker_effect():
    payload = make_heartbeat(now=1_000.0, release="abc123")

    assert heartbeat_is_fresh(
        payload,
        now=1_120.0,
        max_age_seconds=180,
        expected_release="abc123",
    )
    assert not heartbeat_is_fresh(
        payload,
        now=1_181.0,
        max_age_seconds=180,
        expected_release="abc123",
    )
    assert not heartbeat_is_fresh(
        payload,
        now=999.0,
        max_age_seconds=180,
        expected_release="abc123",
    )
    assert not heartbeat_is_fresh(
        payload,
        now=1_120.0,
        max_age_seconds=180,
        expected_release="other-release",
    )


def test_heartbeat_rejects_missing_or_malformed_state():
    assert not heartbeat_is_fresh(
        None, now=1_000.0, max_age_seconds=180, expected_release="abc123"
    )
    assert not heartbeat_is_fresh(
        "not-json", now=1_000.0, max_age_seconds=180, expected_release="abc123"
    )
    assert not heartbeat_is_fresh(
        json.dumps({"timestamp": "later", "release": "abc123"}),
        now=1_000.0,
        max_age_seconds=180,
        expected_release="abc123",
    )


@pytest.mark.anyio
async def test_publish_and_check_heartbeat_use_the_shared_redis_effect(
    monkeypatch,
):
    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def set(self, key: str, value: str, *, ex: int) -> None:
            assert ex == 360
            self.values[key] = value

        async def get(self, key: str) -> str | None:
            return self.values.get(key)

        async def aclose(self) -> None:
            return None

    redis = FakeRedis()

    class RedisFactory:
        @staticmethod
        def from_url(url: str, *, decode_responses: bool) -> FakeRedis:
            assert decode_responses
            return redis

    monkeypatch.setattr(taskiq_health, "Redis", RedisFactory)
    monkeypatch.setenv("CHEESE_TASKIQ_RELEASE", "abc123")

    result = await taskiq_health.publish_heartbeat()

    assert json.loads(result["heartbeat"])["release"] == "abc123"
    assert await taskiq_health.check_heartbeat()
