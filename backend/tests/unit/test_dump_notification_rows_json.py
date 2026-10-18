"""Chunked notification-row serialization yields between batches and produces
byte-identical output to per-row `NotificationOut.from_row`."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.delivery_events import NotificationType
from app.domain.notification.models import Notification, NotificationLevel
from app.domain.notification.schemas import NotificationOut

PROJECT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
CONVERSATION_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
BASE_TIME = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
KINDS = (
    NotificationType.CHANGE_ALERT,
    NotificationType.DECISION_REQUEST,
    NotificationType.MENTION,
)


def _make_rows(count: int) -> list[Notification]:
    return [
        Notification(
            id=i + 1,
            receiver_id=i + 1 if i % 2 else None,
            recipient_handle=f"user-{i % 7}" if i % 5 else None,
            type=KINDS[i % len(KINDS)],
            project_id=PROJECT_ID if i % 4 else None,
            conversation_id=CONVERSATION_ID if i % 3 else None,
            level=NotificationLevel.strong
            if i % 6 == 0
            else (NotificationLevel.light if i % 3 == 0 else None),
            title=f"通知 {i}" if i % 8 else None,
            body=f"正文 {i}" if i % 9 else None,
            metadata_payload={"options": ["是", "否"], "idx": i} if i % 2 else None,
            read=bool(i % 2),
            resolved_at=BASE_TIME if i % 10 == 0 else None,
            created_at=BASE_TIME,
        )
        for i in range(count)
    ]


def _expected(rows: list[Notification]) -> list[dict]:
    return [NotificationOut.from_row(row).model_dump(mode="json") for row in rows]


def _as_bytes(rows: list[dict]) -> bytes:
    return json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")


def test_dump_notification_rows_json_100_rows_matches_per_row() -> None:
    from app.domain.notification.adapters import dump_notification_rows_json

    rows = _make_rows(100)

    actual = asyncio.run(dump_notification_rows_json(rows))

    assert actual == _expected(rows)


def test_dump_notification_rows_json_1000_rows_yields_every_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.notification import adapters

    rows = _make_rows(1_000)
    expected = _expected(rows)

    real_sleep = asyncio.sleep
    sleep_zero_calls = 0
    dumped_at_each_yield: list[int] = []
    dumped_so_far = 0
    real_dump = adapters._NOTIFICATION_LIST_ADAPTER.dump_python

    def tracking_dump(batch, *args, **kwargs):
        nonlocal dumped_so_far
        res = real_dump(batch, *args, **kwargs)
        dumped_so_far += len(res)
        return res

    async def counting_sleep(delay: float):
        nonlocal sleep_zero_calls
        if delay == 0:
            sleep_zero_calls += 1
            dumped_at_each_yield.append(dumped_so_far)
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)
    monkeypatch.setattr(
        adapters._NOTIFICATION_LIST_ADAPTER, "dump_python", tracking_dump
    )

    actual = asyncio.run(adapters.dump_notification_rows_json(rows, chunk=500))

    # 1,000 rows / 500 per chunk = 2 chunks -> 1 inter-chunk sleep(0) yield,
    # with the first 500 rows already dumped before it.
    assert sleep_zero_calls == 1
    assert dumped_at_each_yield == [500]
    assert actual == expected
    assert _as_bytes(actual) == _as_bytes(expected)


def test_dump_notification_rows_json_1200_rows_keeps_the_partial_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full chunks followed by a short tail; dropping the tail must not pass."""
    from app.domain.notification import adapters

    rows = _make_rows(1_200)
    expected = _expected(rows)

    real_sleep = asyncio.sleep
    yields = 0
    dumped_at_each_yield: list[int] = []
    dumped_so_far = 0
    real_dump = adapters._NOTIFICATION_LIST_ADAPTER.dump_python

    def tracking_dump(batch, *args, **kwargs):
        nonlocal dumped_so_far
        res = real_dump(batch, *args, **kwargs)
        dumped_so_far += len(res)
        return res

    async def counting_sleep(delay: float):
        nonlocal yields
        if delay == 0:
            yields += 1
            dumped_at_each_yield.append(dumped_so_far)
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)
    monkeypatch.setattr(
        adapters._NOTIFICATION_LIST_ADAPTER, "dump_python", tracking_dump
    )

    actual = asyncio.run(adapters.dump_notification_rows_json(rows, chunk=500))

    assert len(actual) == len(rows)
    assert yields == 2
    assert dumped_at_each_yield == [500, 1_000]
    assert actual == expected


def test_dump_notification_rows_json_honours_a_non_default_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chunk argument, not a hardcoded 500, decides where the helper yields."""
    from app.domain.notification import adapters

    rows = _make_rows(20)
    expected = _expected(rows)

    real_sleep = asyncio.sleep
    yields = 0

    async def counting_sleep(delay: float):
        nonlocal yields
        if delay == 0:
            yields += 1
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)

    actual = asyncio.run(adapters.dump_notification_rows_json(rows, chunk=7))

    # 20 rows / 7 per chunk = 3 chunks -> 2 inter-chunk yields.
    assert yields == 2
    assert actual == expected


def test_dump_notification_rows_json_empty() -> None:
    from app.domain.notification.adapters import dump_notification_rows_json

    assert asyncio.run(dump_notification_rows_json([])) == []
