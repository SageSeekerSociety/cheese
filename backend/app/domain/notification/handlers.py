import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.models import Notification, NotificationType
from app.domain.notification.repositories import NotificationRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NotificationDelivery:
    recipient_id: int
    type: NotificationType
    payload: dict[str, Any]
    is_aggregated_finalization: bool = False
    #: 这一笔投递的身份（`delivery/ledger.py` 的去重键）。落在收件箱那一行上，所以
    #: 补发撞上唯一约束什么也不发生 —— 「恰好一次」由它保证。None = 聚合窗口收口发
    #: 出的那一批，它们代表的是已经在库里的行，不是一次新的投递。
    delivery_key: str | None = None


class NotificationChannelHandler(Protocol):
    name: str

    async def send_batch(self, deliveries: Sequence[NotificationDelivery]) -> None: ...


class InAppNotificationHandler:
    name = "in-app"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def send_batch(self, deliveries: Sequence[NotificationDelivery]) -> None:
        if not deliveries:
            return

        now = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        for delivery in deliveries:
            if delivery.is_aggregated_finalization:
                # Aggregated rows already exist in DB; finalization just flips the flag.
                continue
            rows.append(
                {
                    "receiver_id": delivery.recipient_id,
                    "type": delivery.type,
                    "metadata_payload": delivery.payload,
                    "read": False,
                    "is_aggregatable": False,
                    "aggregation_key": None,
                    "aggregate_until": None,
                    "finalized": True,
                    "delivery_key": delivery.delivery_key,
                    "version": 0,
                    # 入库的时刻，不是事件发生的时刻：收件箱按 `created_at DESC`
                    # 翻页，落一个旧时间戳会把这一条插进二十分钟前的位置 —— 未读数
                    # 加一，人打开收件箱却看不到新东西。事件发生的时刻记在账本的
                    # `deliveries.event_at` 上。
                    "created_at": now,
                    "updated_at": now,
                    "deleted_at": None,
                }
            )

        if not rows:
            return

        # 插不进去就是它已经在了 —— 上一次发送在回写 `sent_at` 之前没算送到，补发
        # 再来一次，收件人仍然只看到一条。每一条写进来的通知都带着去重键：发通知
        # 只有账本这一处（I11），而它的入参里那个键是必填的。
        await self._session.execute(
            pg_insert(Notification)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["delivery_key"])
        )
        await self._session.flush()


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
                "Failed to enqueue notification batch into Redis queue %s",
                self._queue_key,
            )


class NotificationEventHandler:
    def __init__(
        self,
        session: AsyncSession,
        *,
        channel_handlers: Sequence[NotificationChannelHandler] | None = None,
    ) -> None:
        self._session = session
        self._repo = NotificationRepository(session=session)
        self._channel_handlers: tuple[NotificationChannelHandler, ...]
        if channel_handlers:
            self._channel_handlers = tuple(channel_handlers)
        else:
            self._channel_handlers = (InAppNotificationHandler(session=session),)

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
        await self.dispatch(deliveries)
        return finalized

    async def dispatch(self, deliveries: Sequence[NotificationDelivery]) -> bool:
        """把这一批交给每个渠道。返回值是「每个渠道都收下了」。

        投递账本靠这个返回值决定回不回写 `sent_at`：吞掉一个渠道的异常还报成功，
        账本就会记下一笔根本没发出去的投递，而那正是补发要救的那一档。
        """
        if not deliveries:
            return True
        accepted = True
        for handler in self._channel_handlers:
            try:
                # A savepoint, not just a try/except: a DB-writing handler
                # (in-app) can fail mid-flush (e.g. a non-ASCII payload against
                # a non-UTF8 database), which leaves the whole shared session's
                # transaction aborted in Postgres even though the Python
                # exception is caught here — silently breaking the caller's own
                # later commit. The savepoint scopes that failure to just this
                # handler's writes, so the caller's transaction stays usable.
                async with self._session.begin_nested():
                    await handler.send_batch(deliveries)
            except Exception:
                logger.exception("Notification handler %s failed", handler.name)
                accepted = False
        return accepted
