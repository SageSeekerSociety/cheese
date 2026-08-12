import importlib
import json
import sys
from collections import defaultdict
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class FakeBroker:
    def task(self, *args, **kwargs):
        def decorate(function):
            return function

        return decorate


class FakeLock:
    async def acquire(self, blocking=False):
        return True

    async def extend(self, seconds, replace_ttl=False):
        return True

    async def release(self):
        return None


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands: list[tuple] = []

    def lrem(self, key, count, value):
        self.commands.append(("lrem", key, count, value))
        return self

    def rpush(self, key, value):
        self.commands.append(("rpush", key, value))
        return self

    async def execute(self):
        results = []
        for command, *args in self.commands:
            results.append(await getattr(self.redis, command)(*args))
        return results


class FakeRedis:
    def __init__(self, queue_key: str, item: str):
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.lists[queue_key] = [item]
        self.closed = False

    async def lpop(self, key, count=None):
        values = self.lists[key]
        if not values:
            return None
        if count is None:
            return values.pop(0)
        result = values[:count]
        del values[:count]
        return result

    async def lmove(self, source, destination, wherefrom, whereto):
        values = self.lists[source]
        if not values:
            return None
        value = values.pop(0 if wherefrom == "LEFT" else -1)
        if whereto == "LEFT":
            self.lists[destination].insert(0, value)
        else:
            self.lists[destination].append(value)
        return value

    async def lrem(self, key, count, value):
        removed = 0
        remaining = []
        for item in self.lists[key]:
            if item == value and (count == 0 or removed < count):
                removed += 1
            else:
                remaining.append(item)
        self.lists[key] = remaining
        return removed

    async def llen(self, key):
        return len(self.lists[key])

    async def rpush(self, key, value):
        self.lists[key].append(value)
        return len(self.lists[key])

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    def lock(self, name, timeout):
        return FakeLock()

    async def aclose(self):
        self.closed = True


class FakeSession:
    def __init__(self, email: str | None = "user@example.com"):
        self.email = email

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.email)


@pytest.fixture
def task_module(monkeypatch):
    broker_module = ModuleType("app.core.taskiq_broker")
    broker_module.broker = FakeBroker()
    broker_module.scheduler = SimpleNamespace(sources=[])
    monkeypatch.setitem(sys.modules, "app.core.taskiq_broker", broker_module)
    sys.modules.pop("app.core.taskiq_tasks", None)
    module = importlib.import_module("app.core.taskiq_tasks")
    yield module
    sys.modules.pop("app.core.taskiq_tasks", None)


def _notification_item() -> str:
    return json.dumps({"recipientId": 7, "type": "mention"})


async def _run_queue_task(
    monkeypatch, task_module, *, send_result, retry=0, start_in_processing=False
):
    import redis.asyncio as redis_asyncio

    from app.core import email as email_module
    from app.core.config import settings
    from app.db import session as session_module

    queue_key = settings.notification_email_queue_key
    payload = json.loads(_notification_item())
    if retry:
        payload["_emailRetry"] = retry
    redis = FakeRedis(queue_key, json.dumps(payload))
    if start_in_processing:
        redis.lists[f"{queue_key}:processing"] = redis.lists[queue_key]
        redis.lists[queue_key] = []
    sender = SimpleNamespace(send=AsyncMock(return_value=send_result))

    class RedisFactory:
        @staticmethod
        def from_url(url, decode_responses=True):
            return redis

    monkeypatch.setattr(redis_asyncio, "Redis", RedisFactory)
    monkeypatch.setattr(email_module, "get_email_sender", lambda: sender)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", FakeSession)

    result = await task_module.process_email_queue_task()
    return result, redis, sender


@pytest.mark.anyio
async def test_failed_smtp_delivery_stays_retryable_and_is_not_processed(
    monkeypatch, task_module
):
    result, redis, sender = await _run_queue_task(
        monkeypatch, task_module, send_result=False
    )

    queue_key = "cheese:notifications:email"
    assert result["processed"] == 0
    assert result["retried"] == 1
    assert len(redis.lists[queue_key]) == 1
    assert json.loads(redis.lists[queue_key][0])["_emailRetry"] == 1
    assert redis.lists[f"{queue_key}:processing"] == []
    sender.send.assert_awaited_once()


@pytest.mark.anyio
async def test_successful_smtp_delivery_is_acknowledged(monkeypatch, task_module):
    result, redis, sender = await _run_queue_task(
        monkeypatch, task_module, send_result=True
    )

    queue_key = "cheese:notifications:email"
    assert result == {"processed": 1, "retried": 0, "dead_lettered": 0}
    assert redis.lists[queue_key] == []
    assert redis.lists[f"{queue_key}:processing"] == []
    sender.send.assert_awaited_once()


@pytest.mark.anyio
async def test_exhausted_smtp_retry_moves_to_dead_letter(monkeypatch, task_module):
    from app.core.config import settings

    result, redis, _sender = await _run_queue_task(
        monkeypatch,
        task_module,
        send_result=False,
        retry=settings.notification_email_max_retries - 1,
    )

    queue_key = settings.notification_email_queue_key
    assert result == {"processed": 0, "retried": 0, "dead_lettered": 1}
    assert redis.lists[queue_key] == []
    dead = redis.lists[f"{queue_key}:dead"]
    assert len(dead) == 1
    assert json.loads(dead[0])["_emailRetry"] == settings.notification_email_max_retries


@pytest.mark.anyio
async def test_item_claimed_by_a_crashed_consumer_is_recovered(
    monkeypatch, task_module
):
    from app.core.config import settings

    result, redis, _sender = await _run_queue_task(
        monkeypatch,
        task_module,
        send_result=True,
        start_in_processing=True,
    )

    queue_key = settings.notification_email_queue_key
    assert result["processed"] == 1
    assert redis.lists[queue_key] == []
    assert redis.lists[f"{queue_key}:processing"] == []
