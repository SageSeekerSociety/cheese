"""Unit tests for app.domain.notification.handlers.

Covers InAppNotificationHandler, RedisEmailQueueNotificationHandler and
NotificationEventHandler.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
    NotificationEventHandler,
    RedisEmailQueueNotificationHandler,
)
from app.domain.notification.models import Notification, NotificationType

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# InAppNotificationHandler
# ---------------------------------------------------------------------------


async def _inbox(session, receiver_id: int) -> list[Notification]:
    rows = await session.scalars(
        select(Notification).where(Notification.receiver_id == receiver_id)
    )
    return list(rows)


class TestInAppNotificationHandler:
    @pytest.mark.anyio
    async def test_send_batch_creates_notifications(self, db_factory):
        async with db_factory() as session:
            await InAppNotificationHandler(session).send_batch(
                [
                    NotificationDelivery(
                        recipient_id=10,
                        type=NotificationType.MENTION,
                        payload={"actor": {"id": "1", "type": "user"}},
                    ),
                    NotificationDelivery(
                        recipient_id=20,
                        type=NotificationType.REPLY,
                        payload={"actor": {"id": "2", "type": "user"}},
                    ),
                ]
            )

            (mention,) = await _inbox(session, 10)
            (reply,) = await _inbox(session, 20)
            assert mention.type == NotificationType.MENTION
            assert mention.metadata_payload == {"actor": {"id": "1", "type": "user"}}
            assert reply.type == NotificationType.REPLY

    @pytest.mark.anyio
    async def test_send_batch_skips_aggregated_finalization(self, db_factory):
        """聚合窗口收口的那一条在库里已经有行了，收口只翻标志位，不再写一行。"""
        async with db_factory() as session:
            await InAppNotificationHandler(session).send_batch(
                [
                    NotificationDelivery(
                        recipient_id=10,
                        type=NotificationType.REACTION,
                        payload={},
                        is_aggregated_finalization=True,
                    ),
                ]
            )

            assert await _inbox(session, 10) == []

    @pytest.mark.anyio
    async def test_the_same_delivery_key_lands_once(self, db_factory):
        """同一笔投递发两遍 —— 收件人手里只有一条。

        这是「发出之后、回写确认之前崩掉」那一档的唯一保障：补发会把同一笔再发一
        遍，而账本那一行看起来仍然没发出去。
        """
        async with db_factory() as session:
            handler = InAppNotificationHandler(session)
            delivery = NotificationDelivery(
                recipient_id=10,
                type=NotificationType.ROOM_NOTICE,
                payload={"content": "卡递上来了"},
                delivery_key="event-1:alice",
            )

            await handler.send_batch([delivery])
            await handler.send_batch([delivery])

            assert len(await _inbox(session, 10)) == 1


# ---------------------------------------------------------------------------
# RedisEmailQueueNotificationHandler
# ---------------------------------------------------------------------------


class TestRedisEmailQueueNotificationHandler:
    @pytest.mark.anyio
    async def test_send_batch_empty(self):
        redis = AsyncMock()
        handler = RedisEmailQueueNotificationHandler(redis, queue_key="q")
        await handler.send_batch([])
        redis.rpush.assert_not_awaited()

    @pytest.mark.anyio
    async def test_send_batch_no_redis(self):
        handler = RedisEmailQueueNotificationHandler(None, queue_key="q")
        await handler.send_batch(
            [
                NotificationDelivery(
                    recipient_id=10,
                    type=NotificationType.MENTION,
                    payload={},
                )
            ]
        )
        # No error raised, just returns

    @pytest.mark.anyio
    async def test_send_batch_enqueues_messages(self):
        redis = AsyncMock()
        handler = RedisEmailQueueNotificationHandler(
            redis, queue_key="notifications:email"
        )

        deliveries = [
            NotificationDelivery(
                recipient_id=10,
                type=NotificationType.MENTION,
                payload={"text": "hello"},
            ),
        ]
        await handler.send_batch(deliveries)
        redis.rpush.assert_awaited_once()
        call_args = redis.rpush.call_args
        assert call_args[0][0] == "notifications:email"
        payload = json.loads(call_args[0][1])
        assert payload["recipientId"] == 10
        assert payload["type"] == "MENTION"

    @pytest.mark.anyio
    async def test_send_batch_batches_messages(self):
        redis = AsyncMock()
        handler = RedisEmailQueueNotificationHandler(redis, queue_key="q", batch_size=2)

        deliveries = [
            NotificationDelivery(
                recipient_id=i,
                type=NotificationType.MENTION,
                payload={},
            )
            for i in range(5)
        ]
        await handler.send_batch(deliveries)
        # 5 items with batch_size=2: 2 + 2 + 1 = 3 rpush calls
        assert redis.rpush.await_count == 3

    @pytest.mark.anyio
    async def test_send_batch_handles_redis_error(self):
        redis = AsyncMock()
        redis.rpush.side_effect = ConnectionError("Redis down")
        handler = RedisEmailQueueNotificationHandler(redis, queue_key="q")

        # Should not raise
        await handler.send_batch(
            [
                NotificationDelivery(
                    recipient_id=10,
                    type=NotificationType.MENTION,
                    payload={},
                )
            ]
        )

    @pytest.mark.anyio
    async def test_flush_no_redis(self):
        handler = RedisEmailQueueNotificationHandler(None, queue_key="q")
        await handler._flush(["item1"])
        # No error


# ---------------------------------------------------------------------------
# NotificationEventHandler
# ---------------------------------------------------------------------------


class TestNotificationEventHandler:
    def _make_handler(
        self,
        session=None,
        channel_handlers=None,
    ):
        session = session or AsyncMock()
        session.add = session.add if hasattr(session, "add") else MagicMock()
        # begin_nested() is used as `async with self._session.begin_nested()` — a
        # *sync* call returning an async context manager. AsyncMock makes the call
        # itself a coroutine, so use a plain MagicMock returning an async-CM double.
        nested_cm = MagicMock()
        nested_cm.__aenter__ = AsyncMock(return_value=None)
        nested_cm.__aexit__ = AsyncMock(return_value=None)
        session.begin_nested = MagicMock(return_value=nested_cm)
        return NotificationEventHandler(
            session,
            channel_handlers=channel_handlers,
        )

    @pytest.mark.anyio
    async def test_finalize_expired_none(self):
        session = AsyncMock()
        handler = self._make_handler(session=session)
        handler._repo = AsyncMock()
        handler._repo.find_expired_aggregations.return_value = []

        result = await handler.finalize_expired()
        assert result == []

    @pytest.mark.anyio
    async def test_finalize_expired_marks_finalized(self):
        session = AsyncMock()
        mock_channel = AsyncMock()
        mock_channel.name = "test"
        handler = self._make_handler(session=session, channel_handlers=[mock_channel])
        handler._repo = AsyncMock()

        n = SimpleNamespace(
            id=1,
            receiver_id=10,
            type=NotificationType.REACTION,
            metadata_payload={"test": "data"},
            finalized=False,
            is_aggregatable=True,
            updated_at=NOW,
        )
        handler._repo.find_expired_aggregations.return_value = [n]

        result = await handler.finalize_expired()
        assert len(result) == 1
        assert n.finalized is True
        assert n.is_aggregatable is False
        mock_channel.send_batch.assert_awaited_once()
