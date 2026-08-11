import pytest

from app.domain.notification.dedup import NotificationDeduplicator
from app.domain.notification.events import NotificationTriggerEvent
from app.domain.notification.models import NotificationType


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def set(
        self, name: str, value: bytes, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and name in self._store:
            return None
        self._store[name] = value
        return True


@pytest.mark.anyio
async def test_deduplicator_filters_repeated_payloads() -> None:
    redis = _FakeRedis()
    dedup = NotificationDeduplicator(redis, ttl_seconds=60)
    event = NotificationTriggerEvent(
        source="pytest",
        recipient_ids={1, 2},
        type=NotificationType.TEAM_JOIN_REQUEST,
        payload={"application": {"id": "42", "type": "team_membership_application"}},
    )

    first = await dedup.should_process(event)
    second = await dedup.should_process(event)

    assert first.should_process is True
    assert second.should_process is False


@pytest.mark.anyio
async def test_dedup_key_normalizes_recipient_order() -> None:
    redis = _FakeRedis()
    dedup = NotificationDeduplicator(redis, ttl_seconds=60)

    payload = {"application": {"id": "100", "type": "team_membership_application"}}

    event_a = NotificationTriggerEvent(
        source="pytest",
        recipient_ids={3, 4},
        type=NotificationType.TEAM_INVITATION,
        payload=payload,
    )
    event_b = NotificationTriggerEvent(
        source="pytest",
        recipient_ids={4, 3},
        type=NotificationType.TEAM_INVITATION,
        payload=payload,
    )

    first = await dedup.should_process(event_a)
    second = await dedup.should_process(event_b)

    assert first.should_process is True
    assert second.should_process is False
