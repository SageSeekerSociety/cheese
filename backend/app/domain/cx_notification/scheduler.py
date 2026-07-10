import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.cx_notification.publisher import build_notification_event_handler

logger = logging.getLogger(__name__)


class NotificationAggregationFinalizer:
    """Background task that flushes expired notification aggregations."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        interval_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._interval = max(10, interval_seconds)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="notification-finalizer")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        try:
            while True:
                await self._tick()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise

    async def _tick(self) -> None:
        try:
            async with self._session_factory() as session:
                handler = build_notification_event_handler(session)
                await handler.finalize_expired()
                await session.commit()
        except Exception:
            logger.exception("Notification aggregation finalizer tick failed")
