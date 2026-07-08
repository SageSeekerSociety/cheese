import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.config import notification_config
from app.domain.notification.dedup import NotificationDeduplicator
from app.domain.notification.events import NotificationTriggerEvent
from app.domain.notification.models import Notification, NotificationType
from app.domain.notification.repositories import NotificationRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NotificationDelivery:
    recipient_id: int
    type: NotificationType
    payload: dict[str, Any]
    is_aggregated_finalization: bool = False


class NotificationChannelHandler(Protocol):
    name: str

    async def send_batch(self, deliveries: Sequence[NotificationDelivery]) -> None: ...


class InAppNotificationHandler:
    name = "in-app"

    def __init__(self, session: AsyncSession) -> None:
        self._repo = NotificationRepository(session=session)

    async def send_batch(self, deliveries: Sequence[NotificationDelivery]) -> None:
        if not deliveries:
            return

        now = datetime.now(UTC)
        to_persist: list[Notification] = []
        for delivery in deliveries:
            if delivery.is_aggregated_finalization:
                # Aggregated rows already exist in DB; finalization just flips the flag.
                continue
            notification = Notification(
                receiver_id=delivery.recipient_id,
                type=delivery.type,
                metadata_payload=delivery.payload,
                read=False,
                is_aggregatable=False,
                aggregation_key=None,
                aggregate_until=None,
                finalized=True,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            to_persist.append(notification)

        if not to_persist:
            return

        self._repo._session.add_all(to_persist)
        await self._repo._session.flush()


class RedisEmailQueueNotificationHandler:
    """Batch notifications into a Redis list for async email delivery."""

    name = "redis-email-queue"

    def __init__(
        self,
        redis_client: Redis | None,
        *,
        queue_key: str,
        batch_size: int = 100,
    ) -> None:
        self._redis = redis_client
        self._queue_key = queue_key
        self._batch_size = max(1, batch_size)

    async def send_batch(self, deliveries: Sequence[NotificationDelivery]) -> None:
        if not deliveries or self._redis is None:
            return

        chunks: list[str] = []
        dispatched_at = int(datetime.now(UTC).timestamp() * 1000)

        for delivery in deliveries:
            payload = {
                "recipientId": delivery.recipient_id,
                "type": delivery.type.value,
                "payload": delivery.payload,
                "finalized": delivery.is_aggregated_finalization,
                "dispatchedAt": dispatched_at,
            }
            chunks.append(json.dumps(payload, separators=(",", ":")))

            if len(chunks) >= self._batch_size:
                await self._flush(chunks)
                chunks.clear()

        if chunks:
            await self._flush(chunks)

    async def _flush(self, items: list[str]) -> None:
        if not items or self._redis is None:
            return
        try:
            await self._redis.rpush(self._queue_key, *items)  # type: ignore[misc]
        except Exception:
            logger.exception(
                "Failed to enqueue notification batch into Redis queue %s", self._queue_key
            )


class NotificationEventHandler:
    def __init__(
        self,
        session: AsyncSession,
        *,
        deduplicator: NotificationDeduplicator | None = None,
        channel_handlers: Sequence[NotificationChannelHandler] | None = None,
    ) -> None:
        self._session = session
        self._repo = NotificationRepository(session=session)
        self._deduplicator = deduplicator
        self._channel_handlers: tuple[NotificationChannelHandler, ...]
        if channel_handlers:
            self._channel_handlers = tuple(channel_handlers)
        else:
            self._channel_handlers = (InAppNotificationHandler(session=session),)

    async def handle(self, event: NotificationTriggerEvent) -> None:
        if self._deduplicator is not None:
            dedup = await self._deduplicator.should_process(event)
            if not dedup.should_process:
                logger.debug(
                    "Skip duplicate notification event %s (cache=%s)", event.type, dedup.cache_key
                )
                return

        if notification_config.is_aggregatable(event.type):
            for recipient_id in event.recipient_ids:
                await self._handle_aggregatable(recipient_id, event.type, event.payload)
        else:
            deliveries = [
                NotificationDelivery(
                    recipient_id=recipient_id,
                    type=event.type,
                    payload=event.payload,
                    is_aggregated_finalization=False,
                )
                for recipient_id in event.recipient_ids
            ]
            await self._dispatch_to_handlers(deliveries)

    async def _handle_aggregatable(
        self,
        recipient_id: int,
        type_: NotificationType,
        payload: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC)
        aggregation_key = self._generate_aggregation_key(type_, payload)
        if aggregation_key is None:
            await self._dispatch_to_handlers(
                [
                    NotificationDelivery(
                        recipient_id=recipient_id,
                        type=type_,
                        payload=payload,
                        is_aggregated_finalization=False,
                    )
                ]
            )
            return

        target = await self._repo.find_active_aggregation(
            recipient_id=recipient_id,
            aggregation_key=aggregation_key,
            now=now,
        )

        if target is None:
            aggregate_until = now + notification_config.aggregation_window
            notification = Notification(
                receiver_id=recipient_id,
                type=type_,
                metadata_payload=self._initial_metadata(type_, payload),
                read=False,
                is_aggregatable=True,
                aggregation_key=aggregation_key,
                aggregate_until=aggregate_until,
                finalized=False,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._session.add(notification)
            await self._session.flush()
        else:
            merged = self._merge_metadata(target.metadata_payload or {}, payload, type_)
            target.metadata_payload = merged
            target.updated_at = now
            await self._session.flush()

    async def finalize_expired(self, now: datetime | None = None) -> list[Notification]:
        current = now or datetime.now(UTC)
        expired = await self._repo.find_expired_aggregations(current)
        if not expired:
            return []
        finalized: list[Notification] = []
        for n in expired:
            n.finalized = True
            n.is_aggregatable = False
            n.updated_at = current
            finalized.append(n)
        await self._session.flush()

        deliveries = [
            NotificationDelivery(
                recipient_id=n.receiver_id,
                type=n.type,
                payload=n.metadata_payload or {},
                is_aggregated_finalization=True,
            )
            for n in finalized
        ]
        await self._dispatch_to_handlers(deliveries)
        return finalized

    async def _dispatch_to_handlers(self, deliveries: Sequence[NotificationDelivery]) -> None:
        if not deliveries:
            return
        for handler in self._channel_handlers:
            try:
                # A savepoint, not just a try/except: a DB-writing handler (in-app) can
                # fail mid-flush (e.g. a non-ASCII payload against a non-UTF8 database),
                # which leaves the whole shared session's transaction aborted in Postgres
                # even though the Python exception is caught here — silently breaking the
                # caller's own later commit (e.g. thread/application creation). The
                # savepoint scopes that failure to just this handler's writes, so it rolls
                # back on its own and the caller's transaction stays usable.
                async with self._session.begin_nested():
                    await handler.send_batch(deliveries)
            except Exception:  # pragma: no cover - defensive guardrail
                logger.exception("Notification handler %s failed", handler.name)

    def _generate_aggregation_key(
        self,
        type_: NotificationType,
        payload: dict[str, Any],
    ) -> str | None:
        if type_ is NotificationType.REACTION:
            target = payload.get("target")
            if isinstance(target, dict):
                entity_type = target.get("type")
                entity_id = target.get("id")
                if isinstance(entity_type, str) and isinstance(entity_id, str):
                    return f"REACTION:{entity_type}:{entity_id}"
        return None

    def _initial_metadata(
        self,
        type_: NotificationType,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if type_ is NotificationType.REACTION:
            actor = payload.get("actor")
            actor_id = None
            if isinstance(actor, dict):
                aid = actor.get("id")
                if isinstance(aid, str):
                    actor_id = aid
            reactor_ids: list[str] = []
            if actor_id is not None:
                reactor_ids.append(actor_id)
            metadata = dict(payload)
            metadata["reactorIds"] = reactor_ids
            metadata["totalCount"] = len(reactor_ids) or 1
            return metadata
        return dict(payload)

    def _merge_metadata(
        self,
        existing: dict[str, Any],
        new_payload: dict[str, Any],
        type_: NotificationType,
    ) -> dict[str, Any]:
        if type_ is NotificationType.REACTION:
            current = dict(existing)
            actor = new_payload.get("actor")
            new_reactor_id = None
            if isinstance(actor, dict):
                aid = actor.get("id")
                if isinstance(aid, str):
                    new_reactor_id = aid
            reactor_ids = list(current.get("reactorIds") or [])
            if new_reactor_id is not None and new_reactor_id not in reactor_ids:
                reactor_ids.append(new_reactor_id)
            current["reactorIds"] = reactor_ids
            total = current.get("totalCount")
            try:
                base = int(total)
            except Exception:
                base = len(reactor_ids)
            current["totalCount"] = max(base, len(reactor_ids))
            for k, v in new_payload.items():
                current.setdefault(k, v)
            return current
        merged = dict(existing)
        for k, v in new_payload.items():
            merged.setdefault(k, v)
        return merged
