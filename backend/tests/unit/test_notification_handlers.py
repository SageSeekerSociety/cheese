"""Unit tests for app.domain.notification.handlers.

Covers InAppNotificationHandler, RedisEmailQueueNotificationHandler,
NotificationEventHandler including aggregation logic.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.domain.notification.events import NotificationTriggerEvent
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
    async def test_handle_non_aggregatable_dispatches(self):
        mock_channel = AsyncMock()
        mock_channel.name = "test"
        handler = self._make_handler(channel_handlers=[mock_channel])

        event = NotificationTriggerEvent(
            source="test",
            recipient_ids={10, 20},
            type=NotificationType.MENTION,
            payload={"actor": {"id": "1", "type": "user"}},
        )
        await handler.handle(event)
        mock_channel.send_batch.assert_awaited_once()
        deliveries = mock_channel.send_batch.call_args[0][0]
        recipient_ids = {d.recipient_id for d in deliveries}
        assert recipient_ids == {10, 20}

    @pytest.mark.anyio
    async def test_handle_aggregatable_creates_new_notification(self):
        session = AsyncMock()
        session.add = MagicMock()
        handler = self._make_handler(session=session)

        # Mock find_active_aggregation to return None (no existing aggregation)
        handler._repo = AsyncMock()
        handler._repo.find_active_aggregation.return_value = None

        event = NotificationTriggerEvent(
            source="test",
            recipient_ids={10},
            type=NotificationType.REACTION,
            payload={
                "actor": {"id": "1", "type": "user"},
                "target": {"id": "100", "type": "discussion"},
            },
        )
        await handler.handle(event)
        session.add.assert_called_once()
        notification = session.add.call_args[0][0]
        assert notification.is_aggregatable is True
        assert notification.finalized is False

    @pytest.mark.anyio
    async def test_handle_aggregatable_merges_existing(self):
        session = AsyncMock()
        session.add = MagicMock()
        existing = SimpleNamespace(
            id=1,
            metadata_payload={
                "actor": {"id": "1", "type": "user"},
                "target": {"id": "100", "type": "discussion"},
                "reactorIds": ["1"],
                "totalCount": 1,
            },
            updated_at=NOW,
        )
        handler = self._make_handler(session=session)
        handler._repo = AsyncMock()
        handler._repo.find_active_aggregation.return_value = existing

        event = NotificationTriggerEvent(
            source="test",
            recipient_ids={10},
            type=NotificationType.REACTION,
            payload={
                "actor": {"id": "2", "type": "user"},
                "target": {"id": "100", "type": "discussion"},
            },
        )
        await handler.handle(event)
        # existing notification should have merged metadata
        assert "2" in existing.metadata_payload["reactorIds"]
        assert existing.metadata_payload["totalCount"] >= 2

    @pytest.mark.anyio
    async def test_handle_aggregatable_no_aggregation_key(self):
        """REACTION without proper target falls back to non-aggregated dispatch."""
        mock_channel = AsyncMock()
        mock_channel.name = "test"
        handler = self._make_handler(channel_handlers=[mock_channel])

        event = NotificationTriggerEvent(
            source="test",
            recipient_ids={10},
            type=NotificationType.REACTION,
            payload={"actor": {"id": "1", "type": "user"}},
            # No target -> no aggregation key
        )
        await handler.handle(event)
        mock_channel.send_batch.assert_awaited_once()

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

    def test_generate_aggregation_key_reaction(self):
        handler = self._make_handler()

        key = handler._generate_aggregation_key(
            NotificationType.REACTION,
            {"target": {"id": "100", "type": "discussion"}},
        )
        assert key == "REACTION:discussion:100"

    def test_generate_aggregation_key_non_reaction(self):
        handler = self._make_handler()

        key = handler._generate_aggregation_key(
            NotificationType.MENTION,
            {"target": {"id": "100", "type": "discussion"}},
        )
        assert key is None

    def test_generate_aggregation_key_invalid_target(self):
        handler = self._make_handler()

        key = handler._generate_aggregation_key(
            NotificationType.REACTION,
            {"target": "invalid"},
        )
        assert key is None

    def test_generate_aggregation_key_missing_target_fields(self):
        handler = self._make_handler()

        key = handler._generate_aggregation_key(
            NotificationType.REACTION,
            {"target": {"id": 100, "type": "discussion"}},  # id is int, not str
        )
        assert key is None

    def test_initial_metadata_reaction(self):
        handler = self._make_handler()

        meta = handler._initial_metadata(
            NotificationType.REACTION,
            {
                "actor": {"id": "1", "type": "user"},
                "target": {"id": "100", "type": "discussion"},
            },
        )
        assert meta["reactorIds"] == ["1"]
        assert meta["totalCount"] == 1

    def test_initial_metadata_reaction_no_actor(self):
        handler = self._make_handler()

        meta = handler._initial_metadata(
            NotificationType.REACTION,
            {"target": {"id": "100", "type": "discussion"}},
        )
        assert meta["reactorIds"] == []
        assert meta["totalCount"] == 1

    def test_initial_metadata_non_reaction(self):
        handler = self._make_handler()

        payload = {"key": "value"}
        meta = handler._initial_metadata(NotificationType.MENTION, payload)
        assert meta == {"key": "value"}

    def test_merge_metadata_reaction(self):
        handler = self._make_handler()

        existing = {
            "reactorIds": ["1"],
            "totalCount": 1,
            "target": {"id": "100", "type": "discussion"},
        }
        new_payload = {
            "actor": {"id": "2", "type": "user"},
            "target": {"id": "100", "type": "discussion"},
        }
        merged = handler._merge_metadata(
            existing, new_payload, NotificationType.REACTION
        )
        assert "2" in merged["reactorIds"]
        assert merged["totalCount"] == 2

    def test_merge_metadata_reaction_duplicate_reactor(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": 1}
        new_payload = {"actor": {"id": "1", "type": "user"}}
        merged = handler._merge_metadata(
            existing, new_payload, NotificationType.REACTION
        )
        assert merged["reactorIds"] == ["1"]
        assert merged["totalCount"] == 1

    def test_merge_metadata_reaction_invalid_total(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": "invalid"}
        new_payload = {"actor": {"id": "2", "type": "user"}}
        merged = handler._merge_metadata(
            existing, new_payload, NotificationType.REACTION
        )
        assert merged["totalCount"] == 2  # falls back to len(reactorIds)

    def test_merge_metadata_non_reaction(self):
        handler = self._make_handler()

        existing = {"key1": "value1"}
        new_payload = {"key2": "value2", "key1": "overwrite_attempt"}
        merged = handler._merge_metadata(
            existing, new_payload, NotificationType.MENTION
        )
        assert merged["key1"] == "value1"  # setdefault preserves existing
        assert merged["key2"] == "value2"

    def test_merge_metadata_reaction_no_actor(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": 1}
        new_payload = {"target": {"id": "100", "type": "discussion"}}
        merged = handler._merge_metadata(
            existing, new_payload, NotificationType.REACTION
        )
        assert merged["reactorIds"] == ["1"]

    def test_merge_metadata_reaction_actor_not_dict(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": 1}
        new_payload = {"actor": "string_actor"}
        merged = handler._merge_metadata(
            existing, new_payload, NotificationType.REACTION
        )
        assert merged["reactorIds"] == ["1"]

    def test_merge_metadata_reaction_actor_id_not_str(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": 1}
        new_payload = {"actor": {"id": 2, "type": "user"}}
        merged = handler._merge_metadata(
            existing, new_payload, NotificationType.REACTION
        )
        assert merged["reactorIds"] == ["1"]

    def test_initial_metadata_reaction_actor_not_dict(self):
        handler = self._make_handler()

        meta = handler._initial_metadata(
            NotificationType.REACTION,
            {"actor": "string"},
        )
        assert meta["reactorIds"] == []

    def test_initial_metadata_reaction_actor_id_not_str(self):
        handler = self._make_handler()

        meta = handler._initial_metadata(
            NotificationType.REACTION,
            {"actor": {"id": 123, "type": "user"}},
        )
        assert meta["reactorIds"] == []
