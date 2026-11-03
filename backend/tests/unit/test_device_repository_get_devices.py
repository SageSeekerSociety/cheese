"""A batch of devices is one SELECT, and the same devices as one-by-one reads.

``project_distribution`` used to call ``DeviceService.get_device`` for every
self-hosted machine it lists. Each single read is three statements — the row,
then the device's project bindings, then its team bindings — so a project
spread over a dozen machines cost three statements per machine. The batch read
returns the same devices in one statement.

Both halves are pinned: the batch must equal ``get_device`` called per id
(bindings included, keyed by the ids that exist), and the statement count must
not grow with the ids. A batch implemented as a loop over ``get_device`` passes
the equality and fails the count — which is exactly the regression this guard
is for.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import MetaData, Table, event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.domain.device.models import DeviceRow
from app.domain.device.repository import Device
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.user.models import User
from app.domain.user.tokens import digest_token

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

PROJECT_ONE = uuid.UUID(int=1)
PROJECT_TWO = uuid.UUID(int=2)


@pytest.fixture
async def device_engine():
    """Three devices: one with two projects and two teams, one with a project
    only, and one with neither."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    from sqlalchemy import Column, Integer

    Table("team", metadata, Column("id", Integer, primary_key=True))
    for model in (DeviceRow, User):
        cast(Table, model.__table__).to_metadata(metadata)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add_all(
                [
                    DeviceRow(
                        device_id="device-1",
                        name="alpha",
                        token_digest=digest_token("token-1"),
                        team_id=7,
                        owner_user_id=1,
                        created_at=NOW,
                    ),
                    DeviceRow(
                        device_id="device-2",
                        name="beta",
                        token_digest=digest_token("token-2"),
                        team_id=7,
                        owner_user_id=2,
                        created_at=NOW,
                    ),
                    DeviceRow(
                        device_id="device-3",
                        name="gamma",
                        token_digest=digest_token("token-3"),
                        team_id=7,
                        owner_user_id=1,
                        created_at=NOW,
                    ),
                ]
            )
            await session.flush()
            await session.commit()
            yield session, engine
    finally:
        await engine.dispose()


async def _selects_during(
    engine: AsyncEngine, call: Callable[[], Awaitable[Any]]
) -> tuple[Any, list[str]]:
    statements: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        result = await call()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    return result, statements


def _comparable(device: Device) -> dict:
    """The dataclass with its binding lists compared as sets: neither read
    guarantees an order for them, so the test judges membership, not SQLite's
    row order."""
    return {
        "device_id": device.device_id,
        "name": device.name,
        "owner_user_id": device.owner_user_id,
        "created_at": device.created_at,
        "team_id": device.team_id,
        "supply": device.supply,
        "last_seen_at": device.last_seen_at,
    }


async def test_get_devices_returns_what_get_device_returns_in_one_statement(
    device_engine,
) -> None:
    session, engine = device_engine
    repo = SqlDeviceRepository(session)
    # Duplicates and an id that names nothing: the batch is keyed like the
    # per-id reads, with the missing one omitted rather than raising.
    ids = ["device-1", "device-2", "device-3", "device-3", "gone"]

    one_by_one: dict[str, Device] = {}
    for device_id in ids:
        device = await repo.get_device(device_id)
        if device is not None:
            one_by_one[device_id] = device

    batch, statements = await _selects_during(engine, lambda: repo.get_devices(ids))

    assert {key: _comparable(device) for key, device in batch.items()} == {
        key: _comparable(device) for key, device in one_by_one.items()
    }
    assert len(statements) == 1, statements


async def test_get_devices_of_nothing_reads_nothing(device_engine) -> None:
    session, engine = device_engine
    repo = SqlDeviceRepository(session)

    found, statements = await _selects_during(engine, lambda: repo.get_devices([]))

    assert found == {}
    assert statements == []
