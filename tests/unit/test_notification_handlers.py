"""Unit tests for app.domain.notification.handlers.

Covers InAppNotificationHandler, RedisEmailQueueNotificationHandler,
NotificationEventHandler including aggregation logic.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.notification.dedup import NotificationDedupResult
from app.domain.notification.events import NotificationTriggerEvent
from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
    NotificationEventHandler,
    RedisEmailQueueNotificationHandler,
)
from app.domain.notification.models import NotificationType

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# InAppNotificationHandler
# ---------------------------------------------------------------------------


class TestInAppNotificationHandler:
    @pytest.mark.anyio
    async def test_send_batch_empty(self):
        session = AsyncMock()
        handler = InAppNotificationHandler(session)

        await handler.send_batch([])
        session.flush.assert_not_awaited()

    @pytest.mark.anyio
    async def test_send_batch_creates_notifications(self):
        session = AsyncMock()
        session.add_all = MagicMock()
        handler = InAppNotificationHandler(session)

        deliveries = [
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
        await handler.send_batch(deliveries)
        session.add_all.assert_called_once()
        notifications = session.add_all.call_args[0][0]
        assert len(notifications) == 2
        assert notifications[0].receiver_id == 10
        assert notifications[1].receiver_id == 20

    @pytest.mark.anyio
    async def test_send_batch_skips_aggregated_finalization(self):
        session = AsyncMock()
        session.add_all = MagicMock()
        handler = InAppNotificationHandler(session)

        deliveries = [
            NotificationDelivery(
                recipient_id=10,
                type=NotificationType.REACTION,
                payload={},
                is_aggregated_finalization=True,
            ),
        ]
        await handler.send_batch(deliveries)
        # No notifications to persist since all are finalization
        session.add_all.assert_not_called()


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
        await handler.send_batch([
            NotificationDelivery(
                recipient_id=10,
                type=NotificationType.MENTION,
                payload={},
            )
        ])
        # No error raised, just returns

    @pytest.mark.anyio
    async def test_send_batch_enqueues_messages(self):
        redis = AsyncMock()
        handler = RedisEmailQueueNotificationHandler(redis, queue_key="notifications:email")

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
        handler = RedisEmailQueueNotificationHandler(
            redis, queue_key="q", batch_size=2
        )

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
        await handler.send_batch([
            NotificationDelivery(
                recipient_id=10,
                type=NotificationType.MENTION,
                payload={},
            )
        ])

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
        deduplicator=None,
        channel_handlers=None,
    ):
        session = session or AsyncMock()
        session.add = session.add if hasattr(session, "add") else MagicMock()
        return NotificationEventHandler(
            session,
            deduplicator=deduplicator,
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
    async def test_handle_with_dedup_skip(self):
        dedup = AsyncMock()
        dedup.should_process.return_value = NotificationDedupResult(
            should_process=False, cache_key="test_key"
        )
        mock_channel = AsyncMock()
        mock_channel.name = "test"
        handler = self._make_handler(deduplicator=dedup, channel_handlers=[mock_channel])

        event = NotificationTriggerEvent(
            source="test",
            recipient_ids={10},
            type=NotificationType.MENTION,
            payload={},
        )
        await handler.handle(event)
        mock_channel.send_batch.assert_not_awaited()

    @pytest.mark.anyio
    async def test_handle_with_dedup_process(self):
        dedup = AsyncMock()
        dedup.should_process.return_value = NotificationDedupResult(
            should_process=True, cache_key="test_key"
        )
        mock_channel = AsyncMock()
        mock_channel.name = "test"
        handler = self._make_handler(deduplicator=dedup, channel_handlers=[mock_channel])

        event = NotificationTriggerEvent(
            source="test",
            recipient_ids={10},
            type=NotificationType.MENTION,
            payload={},
        )
        await handler.handle(event)
        mock_channel.send_batch.assert_awaited_once()

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
            {"actor": {"id": "1", "type": "user"}, "target": {"id": "100", "type": "discussion"}},
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
        merged = handler._merge_metadata(existing, new_payload, NotificationType.REACTION)
        assert "2" in merged["reactorIds"]
        assert merged["totalCount"] == 2

    def test_merge_metadata_reaction_duplicate_reactor(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": 1}
        new_payload = {"actor": {"id": "1", "type": "user"}}
        merged = handler._merge_metadata(existing, new_payload, NotificationType.REACTION)
        assert merged["reactorIds"] == ["1"]
        assert merged["totalCount"] == 1

    def test_merge_metadata_reaction_invalid_total(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": "invalid"}
        new_payload = {"actor": {"id": "2", "type": "user"}}
        merged = handler._merge_metadata(existing, new_payload, NotificationType.REACTION)
        assert merged["totalCount"] == 2  # falls back to len(reactorIds)

    def test_merge_metadata_non_reaction(self):
        handler = self._make_handler()

        existing = {"key1": "value1"}
        new_payload = {"key2": "value2", "key1": "overwrite_attempt"}
        merged = handler._merge_metadata(existing, new_payload, NotificationType.MENTION)
        assert merged["key1"] == "value1"  # setdefault preserves existing
        assert merged["key2"] == "value2"

    def test_merge_metadata_reaction_no_actor(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": 1}
        new_payload = {"target": {"id": "100", "type": "discussion"}}
        merged = handler._merge_metadata(existing, new_payload, NotificationType.REACTION)
        assert merged["reactorIds"] == ["1"]

    def test_merge_metadata_reaction_actor_not_dict(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": 1}
        new_payload = {"actor": "string_actor"}
        merged = handler._merge_metadata(existing, new_payload, NotificationType.REACTION)
        assert merged["reactorIds"] == ["1"]

    def test_merge_metadata_reaction_actor_id_not_str(self):
        handler = self._make_handler()

        existing = {"reactorIds": ["1"], "totalCount": 1}
        new_payload = {"actor": {"id": 2, "type": "user"}}
        merged = handler._merge_metadata(existing, new_payload, NotificationType.REACTION)
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
