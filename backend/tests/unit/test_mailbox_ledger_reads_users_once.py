"""`Ledger.record` resolves mailbox recipients with one `user` table query.

Previously `Ledger.record` called `user_by_handle` inside the recipient loop,
issuing one `SELECT ... FROM "user"` per mailbox recipient. It now batches the
mailbox handles through `users_by_handle` before the loop while preserving
recipient order, field values, and the skipping of missing, deleted, or
non-mailbox handles.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Integer,
    MetaData,
    Table,
    event,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.domain.addressing import REASON_REVIEWER, Addressed, Recipient
from app.domain.delivery.ledger import DeliveryEvent, Ledger, Pending
from app.domain.delivery.models import Delivery
from app.domain.delivery_events import NotificationType, dedup_key
from app.domain.happenings.models import Happening
from app.domain.user.models import User

pytestmark = pytest.mark.anyio

EVENT_AT = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
RECORDED_AT = datetime(2026, 10, 18, 9, 0, 5, tzinfo=UTC)

_USER_FROM = re.compile(r'\bFROM\s+"?user"?\b', re.IGNORECASE)


@pytest.fixture
async def ledger_sql():
    """In-memory SQLite engine and session with the tables a record writes."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    for model in (User, Delivery, Happening):
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(
                    col.name,
                    JSON()
                    if isinstance(col.type, JSONB)
                    # SQLite numbers only an INTEGER primary key itself.
                    else Integer()
                    if isinstance(col.type, BigInteger)
                    else col.type,
                    primary_key=col.primary_key,
                    nullable=col.nullable,
                    unique=col.unique,
                    server_default=col.server_default,
                )
                for col in model.__table__.columns
            ),
        )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for user_id, handle in (
                (101, "alice"),
                (102, "bob"),
                (103, "carol"),
                (104, "dave"),
                (105, "erin"),
            ):
                session.add(
                    User(
                        id=user_id,
                        username=handle,
                        email=f"{handle}@example.invalid",
                        hashed_password="x",
                        created_at=EVENT_AT,
                        updated_at=EVENT_AT,
                    )
                )
            session.add(
                User(
                    id=106,
                    username="gone",
                    email="gone@example.invalid",
                    hashed_password="x",
                    created_at=EVENT_AT,
                    updated_at=EVENT_AT,
                    deleted_at=EVENT_AT,
                )
            )
            await session.commit()
            yield session, engine
    finally:
        await engine.dispose()


def _addressed(*handles: str) -> Addressed:
    return Addressed(
        tuple(Recipient(handle=h, reason=REASON_REVIEWER) for h in handles)
    )


def _event(
    *,
    event_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> DeliveryEvent:
    return DeliveryEvent(
        id=event_id or uuid.uuid4(),
        type=NotificationType.ROOM_NOTICE,
        payload=payload or {"content": "卡递上来了，等你验收", "topicId": "t-1"},
        occurred_at=EVENT_AT,
    )


async def _user_selects_during(
    engine: AsyncEngine,
    call: Callable[[], Awaitable[Any]],
) -> tuple[Any, list[str]]:
    statements: list[str] = []

    def count_user_reads(_conn, _cursor, statement: str, *_rest) -> None:
        if statement.lstrip().upper().startswith("SELECT") and _USER_FROM.search(
            statement
        ):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count_user_reads)
    try:
        result = await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_user_reads)
    return result, statements


async def test_five_recipients_read_user_table_once_and_match_every_ledger_field(
    ledger_sql,
) -> None:
    session, engine = ledger_sql
    handles = ("alice", "bob", "carol", "dave", "erin")
    expected_ids = {
        "alice": 101,
        "bob": 102,
        "carol": 103,
        "dave": 104,
        "erin": 105,
    }
    ev = _event()
    ledger = Ledger(session, now=lambda: RECORDED_AT)

    pending, user_selects = await _user_selects_during(
        engine,
        lambda: ledger.record(ev, _addressed(*handles)),
    )
    await session.commit()

    assert len(user_selects) == 1, user_selects

    rows = list(
        await session.scalars(select(Delivery).order_by(Delivery.recipient_handle))
    )
    rows_by_handle = {row.recipient_handle: row for row in rows}
    assert len(rows) == 5

    assert [item.receiver_id for item in pending] == [expected_ids[h] for h in handles]
    for handle, item in zip(handles, pending, strict=True):
        row = rows_by_handle[handle]
        expected_key = dedup_key(ev.id, handle)
        assert item == Pending(
            id=row.id,
            receiver_id=expected_ids[handle],
            type=NotificationType.ROOM_NOTICE,
            payload=ev.payload,
            dedup_key=expected_key,
        )
        assert row.event_id == ev.id
        assert row.recipient_handle == handle
        assert row.receiver_id == expected_ids[handle]
        assert row.external_channels is True
        assert row.agent_instance_id is None
        assert row.conversation_id is None
        assert row.state == "pending"
        assert row.attempt_id is None
        assert row.lease_until is None
        assert row.retry_at is None
        assert row.last_error is None
        assert row.dedup_key == expected_key
        assert row.type == NotificationType.ROOM_NOTICE.value
        assert row.payload == ev.payload
        assert row.event_at.replace(tzinfo=UTC) == EVENT_AT
        assert row.recorded_at.replace(tzinfo=UTC) == RECORDED_AT
        assert row.sent_at is None
        assert row.attempts == 0


async def test_missing_deleted_and_agent_handles_are_skipped_in_one_user_query(
    ledger_sql,
) -> None:
    session, engine = ledger_sql
    handles = ("carol", "ghost", "gone", "cheese-c82aeb40555a", "alice")
    ev = _event()
    ledger = Ledger(session, now=lambda: RECORDED_AT)

    pending, user_selects = await _user_selects_during(
        engine,
        lambda: ledger.record(ev, _addressed(*handles)),
    )
    await session.commit()

    assert len(user_selects) == 1, user_selects
    assert [item.receiver_id for item in pending] == [103, 101]
    assert [item.dedup_key for item in pending] == [
        dedup_key(ev.id, "carol"),
        dedup_key(ev.id, "alice"),
    ]
    rows = list(await session.scalars(select(Delivery)))
    assert {row.recipient_handle for row in rows} == {"carol", "alice"}


async def test_non_mailbox_recipients_do_not_query_user_table(ledger_sql) -> None:
    session, engine = ledger_sql
    ev = _event()
    ledger = Ledger(session, now=lambda: RECORDED_AT)

    pending, user_selects = await _user_selects_during(
        engine,
        lambda: ledger.record(ev, _addressed("cheese-c82aeb40555a")),
    )

    assert pending == []
    assert user_selects == []
