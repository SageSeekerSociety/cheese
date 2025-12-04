from __future__ import annotations

from taskiq import TaskiqScheduler
from taskiq_redis import RedisAsyncResultBackend, ListQueueBroker

from app.core.config import settings


broker = ListQueueBroker(
    url=settings.redis_url,
    queue_name="cheese:tasks",
)

broker.with_result_backend(
    RedisAsyncResultBackend(
        redis_url=settings.redis_url,
        result_ex_time=3600,
    )
)

scheduler = TaskiqScheduler(broker=broker, sources=[])
