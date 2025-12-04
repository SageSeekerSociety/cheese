from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from redis.asyncio import Redis

from app.domain.notification.events import NotificationTriggerEvent


@dataclass(slots=True)
class NotificationDedupResult:
    should_process: bool
    cache_key: str | None = None


class NotificationDeduplicator:
    """Redis-backed deduplication for notification trigger events."""

    def __init__(
        self,
        redis_client: Redis | None,
        *,
        ttl_seconds: int,
        namespace: str = "cheese:notifications:dedup",
    ) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._namespace = namespace

    def _payload_fingerprint(self, event: NotificationTriggerEvent) -> str:
        canon = json.dumps(event.payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha1(canon.encode("utf-8"), usedforsecurity=False).hexdigest()

    def _build_cache_key(self, event: NotificationTriggerEvent) -> str:
        recipients = ",".join(str(rid) for rid in sorted(event.recipient_ids)) or "*"
        fingerprint = self._payload_fingerprint(event)
        return f"{self._namespace}:{event.type.value}:{recipients}:{fingerprint}"

    async def should_process(self, event: NotificationTriggerEvent) -> NotificationDedupResult:
        """Return False when the event has already been processed recently."""

        if self._redis is None:
            return NotificationDedupResult(should_process=True, cache_key=None)

        cache_key = self._build_cache_key(event)

        try:
            was_set = await self._redis.set(cache_key, b"1", ex=self._ttl_seconds, nx=True)
        except Exception:
            # Redis outages must not block notification delivery; fall back to allowing event.
            return NotificationDedupResult(should_process=True, cache_key=cache_key)

        should_process = bool(was_set)
        return NotificationDedupResult(should_process=should_process, cache_key=cache_key)
