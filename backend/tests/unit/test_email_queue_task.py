"""Channel intent survives rollback, interruption and competing consumers.

SQL transactions, claims and acknowledgement are real. Only the external
provider is substituted; no email or browser push leaves this suite.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.domain.delivery.models import ChannelDelivery
from app.domain.notification import maintenance, push_delivery
from app.domain.notification.handlers import NotificationDelivery
from app.domain.notification.models import NotificationType
from app.domain.notification.outbox import ChannelIntentHandler, drain_channel
from app.domain.user.repositories import UserRepository

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def isolated_legacy_queues(monkeypatch):
    from app.core.config import settings

    for channel in ("email", "push"):
        monkeypatch.setattr(
            settings,
            f"notification_{channel}_queue_key",
            f"test:{channel}:{uuid.uuid4()}",
        )


async def _enqueue(db_factory, *, push=False, rollback=False):
    async with db_factory() as session:
        user = await UserRepository(session).create_user(
            username="outbox-user", email="outbox@example.com"
        )
        await session.commit()
        item = NotificationDelivery(
            user.id,
            NotificationType.ROOM_NOTICE,
            {"content": "Ready for your review", "topicTitle": "Room"},
            "event-1:outbox-user",
        )
        handler = ChannelIntentHandler(session, push_enabled=push)
        await handler.send_batch([item, item])
        async with db_factory() as observer:
            assert list(await observer.scalars(select(ChannelDelivery))) == []
        if rollback:
            await session.rollback()
        else:
            await session.commit()


async def _rows(db_factory):
    async with db_factory() as session:
        return list(
            await session.scalars(
                select(ChannelDelivery).order_by(ChannelDelivery.channel)
            )
        )


async def test_rollback_never_contacts_an_external_recipient(db_factory, monkeypatch):
    sender = SimpleNamespace(send=AsyncMock(return_value=True))
    monkeypatch.setattr(maintenance, "get_email_sender", lambda: sender)
    await _enqueue(db_factory, rollback=True)
    assert await maintenance.drain_email_queue(db_factory) == {
        "processed": 0,
        "retried": 0,
        "dead_lettered": 0,
    }
    sender.send.assert_not_awaited()
    assert await _rows(db_factory) == []


async def test_committed_intent_is_deduplicated_and_sent_after_restarting_consumer(
    db_factory, monkeypatch
):
    sender = SimpleNamespace(send=AsyncMock(return_value=True))
    monkeypatch.setattr(maintenance, "get_email_sender", lambda: sender)
    await _enqueue(db_factory)
    assert len(await _rows(db_factory)) == 1
    assert (await maintenance.drain_email_queue(db_factory))["processed"] == 1
    assert (await maintenance.drain_email_queue(db_factory))["processed"] == 0
    sender.send.assert_awaited_once()
    assert (await _rows(db_factory))[0].state == "sent"


async def test_failed_email_does_not_repeat_successful_push(db_factory, monkeypatch):
    sender = SimpleNamespace(send=AsyncMock(return_value=False))
    monkeypatch.setattr(maintenance, "get_email_sender", lambda: sender)
    push = AsyncMock(return_value=0)
    monkeypatch.setattr(push_delivery, "send_push", push)
    await _enqueue(db_factory, push=True)
    assert (await maintenance.drain_email_queue(db_factory))["retried"] == 1
    assert (
        await drain_channel(db_factory, channel="push", batch_size=10, max_attempts=3)
    )["processed"] == 1
    async with db_factory() as session:
        email = await session.scalar(
            select(ChannelDelivery).where(ChannelDelivery.channel == "email")
        )
        email.retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    sender.send.return_value = True
    assert (await maintenance.drain_email_queue(db_factory))["processed"] == 1
    assert (
        await drain_channel(db_factory, channel="push", batch_size=10, max_attempts=3)
    )["processed"] == 0
    push.assert_awaited_once()
    assert all(row.state == "sent" for row in await _rows(db_factory))


async def test_provider_failure_exhausts_a_visible_bounded_retry_budget(
    db_factory, monkeypatch
):
    monkeypatch.setattr(
        maintenance,
        "get_email_sender",
        lambda: SimpleNamespace(send=AsyncMock(return_value=False)),
    )
    await _enqueue(db_factory)
    result = await drain_channel(
        db_factory, channel="email", batch_size=10, max_attempts=1
    )
    assert result["dead_lettered"] == 1
    row = (await _rows(db_factory))[0]
    assert row.state == "dead"
    assert "SMTP" in row.last_error
    assert (
        await drain_channel(db_factory, channel="email", batch_size=10, max_attempts=1)
    )["processed"] == 0


async def test_lost_provider_ack_retries_with_the_same_intent_and_may_duplicate(
    db_factory, monkeypatch
):
    accepted = []

    async def send(**kwargs):
        accepted.append(kwargs)
        if len(accepted) == 1:
            raise asyncio.CancelledError()
        return True

    monkeypatch.setattr(
        maintenance, "get_email_sender", lambda: SimpleNamespace(send=send)
    )
    await _enqueue(db_factory)
    with pytest.raises(asyncio.CancelledError):
        await maintenance.drain_email_queue(db_factory)
    original = (await _rows(db_factory))[0]
    assert original.state == "sending"
    async with db_factory() as session:
        row = await session.get(ChannelDelivery, original.id)
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (await maintenance.drain_email_queue(db_factory))["processed"] == 1
    row = (await _rows(db_factory))[0]
    assert row.id == original.id and row.attempts == 2
    assert len(accepted) == 2, "SMTP acceptance is not an idempotent receiver"


async def test_an_inflight_send_releases_database_and_excludes_another_consumer(
    db_factory, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(**kwargs):
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(
        maintenance, "get_email_sender", lambda: SimpleNamespace(send=send)
    )
    await _enqueue(db_factory)
    worker = asyncio.create_task(maintenance.drain_email_queue(db_factory))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        async with db_factory() as observer:
            row = await asyncio.wait_for(
                observer.scalar(select(ChannelDelivery).with_for_update()), 2
            )
            assert row.state == "sending"
            await observer.rollback()
        assert (await maintenance.drain_email_queue(db_factory))["processed"] == 0
    finally:
        release.set()
        await worker


async def test_only_participant_action_notifications_create_push_intent(db_factory):
    async with db_factory() as session:
        handler = ChannelIntentHandler(session, push_enabled=True)
        await handler.send_batch(
            [
                NotificationDelivery(
                    10,
                    NotificationType.ROOM_NOTICE,
                    {"content": "Please review"},
                    "notice:10",
                ),
                NotificationDelivery(
                    10, NotificationType.MENTION, {"content": "Mention"}, "mention:10"
                ),
            ]
        )
        await session.commit()
    rows = await _rows(db_factory)
    pushes = [row for row in rows if row.channel == "push"]
    assert len(pushes) == 1 and pushes[0].payload["title"] == "Please review"


def test_the_email_escapes_user_text_and_preserves_the_destination():
    subject, body = maintenance._compose_email(
        {
            "type": "ROOM_NOTICE",
            "payload": {
                "content": "<script>bad()</script>",
                "topicId": "room-1",
                "topicTitle": "Review",
            },
        }
    )
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert maintenance.settings.frontend_url in body


async def test_legacy_migration_respects_old_lock_and_recovers_commit_before_ack(
    db_factory, monkeypatch
):
    import json
    import uuid

    from redis.asyncio import Redis

    from app.core.config import settings
    from app.domain.notification.legacy_queue import import_legacy_queue

    key = f"test:legacy:{uuid.uuid4()}"
    monkeypatch.setattr(settings, "notification_email_queue_key", key)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    payload = json.dumps(
        {"recipientId": 17, "type": "ROOM_NOTICE", "payload": {}, "_emailRetry": 1}
    )
    await redis.rpush(key, payload)
    lock = redis.lock(f"{key}:consumer-lock", timeout=90)
    assert await lock.acquire(blocking=False)
    try:
        assert (
            await import_legacy_queue(
                db_factory, channel="email", batch_size=10, redis=redis
            )
            == 0
        )
        assert await redis.llen(key) == 1
        assert await _rows(db_factory) == []
    finally:
        await lock.release()
    original_ack = redis.lrem

    async def crash_ack(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(redis, "lrem", crash_ack)
    with pytest.raises(asyncio.CancelledError):
        await import_legacy_queue(
            db_factory, channel="email", batch_size=10, redis=redis
        )
    first = await _rows(db_factory)
    assert len(first) == 1 and first[0].attempts == 1
    assert await redis.llen(f"{key}:sql-migration") == 1
    monkeypatch.setattr(redis, "lrem", original_ack)
    assert (
        await import_legacy_queue(
            db_factory, channel="email", batch_size=10, redis=redis
        )
        == 1
    )
    rows = await _rows(db_factory)
    assert [row.id for row in rows] == [first[0].id]
    assert await redis.llen(f"{key}:sql-migration") == 0
    await redis.aclose()
