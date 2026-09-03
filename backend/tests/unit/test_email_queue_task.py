"""The email queue's claim/ack contract, exercised against a fake Redis.

Nothing else in the platform reads the queue this drains, so every property
below is the difference between mail sent and mail lost: an unacknowledged
claim must come back, a failed send must stay retryable, and a send that has
run out of retries must land somewhere a person can look rather than being
dropped.
"""

import json
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


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


def _notification_item() -> str:
    return json.dumps({"recipientId": 7, "type": "mention"})


async def _drain(
    monkeypatch, *, send_result, retry=0, start_in_processing=False, item=None
):
    from app.core.config import settings
    from app.domain.notification import maintenance

    queue_key = settings.notification_email_queue_key
    payload = json.loads(item or _notification_item())
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

    monkeypatch.setattr(maintenance, "Redis", RedisFactory)
    monkeypatch.setattr(maintenance, "get_email_sender", lambda: sender)

    result = await maintenance.drain_email_queue(FakeSession)
    return result, redis, sender


@pytest.mark.anyio
async def test_failed_smtp_delivery_stays_retryable_and_is_not_processed(monkeypatch):
    result, redis, sender = await _drain(monkeypatch, send_result=False)

    queue_key = "cheese:notifications:email"
    assert result["processed"] == 0
    assert result["retried"] == 1
    assert len(redis.lists[queue_key]) == 1
    assert json.loads(redis.lists[queue_key][0])["_emailRetry"] == 1
    assert redis.lists[f"{queue_key}:processing"] == []
    sender.send.assert_awaited_once()


@pytest.mark.anyio
async def test_successful_smtp_delivery_is_acknowledged(monkeypatch):
    result, redis, sender = await _drain(monkeypatch, send_result=True)

    queue_key = "cheese:notifications:email"
    assert result == {"processed": 1, "retried": 0, "dead_lettered": 0}
    assert redis.lists[queue_key] == []
    assert redis.lists[f"{queue_key}:processing"] == []
    sender.send.assert_awaited_once()


@pytest.mark.anyio
async def test_exhausted_smtp_retry_moves_to_dead_letter(monkeypatch):
    from app.core.config import settings

    result, redis, _sender = await _drain(
        monkeypatch,
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
async def test_item_claimed_by_a_crashed_consumer_is_recovered(monkeypatch):
    from app.core.config import settings

    result, redis, _sender = await _drain(
        monkeypatch,
        send_result=True,
        start_in_processing=True,
    )

    queue_key = settings.notification_email_queue_key
    assert result["processed"] == 1
    assert redis.lists[queue_key] == []
    assert redis.lists[f"{queue_key}:processing"] == []


# --- what the recipient actually receives -----------------------------------
#
# The queue above is careful about not losing mail. These are about the mail
# itself: it was a hardcoded line ("You have a new notification: MENTION") with
# no content, no link and no language the recipient reads, and until #650 wired
# this job up nobody had ever received one, so nobody had seen it.


@pytest.mark.anyio
async def test_the_email_says_what_happened_and_where_to_go(monkeypatch):
    _result, _redis, sender = await _drain(
        monkeypatch,
        send_result=True,
        item=json.dumps(
            {
                "recipientId": 7,
                "type": "MENTION",
                "payload": {"content": "这段能不能再看一眼"},
            }
        ),
    )
    kwargs = sender.send.await_args.kwargs
    # 收件人是在自己的邮箱里读到它的，那里没有任何上下文。
    assert "提到了你" in kwargs["subject"]
    assert "这段能不能再看一眼" in kwargs["body_html"]
    # 而且要回得来：一封说了有事发生却不告诉你去哪看的信，等于没说。
    assert "href=" in kwargs["body_html"]


@pytest.mark.anyio
async def test_the_email_escapes_what_other_people_wrote(monkeypatch):
    """`payload` 装的是别人写的字，而这段 HTML 会落进某个人的邮件客户端。"""
    _result, _redis, sender = await _drain(
        monkeypatch,
        send_result=True,
        item=json.dumps(
            {
                "recipientId": 7,
                "type": "REPLY",
                "payload": {"content": "<script>alert(1)</script>"},
            }
        ),
    )
    body = sender.send.await_args.kwargs["body_html"]
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


@pytest.mark.anyio
async def test_an_aggregated_email_says_it_stands_for_a_batch(monkeypatch):
    """聚合窗口收口发出的那一条代表的是一批。不说明的话，收件人会以为平台把
    其余几十条弄丢了。"""
    _result, _redis, sender = await _drain(
        monkeypatch,
        send_result=True,
        item=json.dumps(
            {"recipientId": 7, "type": "REACTION", "payload": {}, "finalized": True}
        ),
    )
    assert "合并成了一条" in sender.send.await_args.kwargs["subject"]
