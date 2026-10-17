"""Machine and integration statistics share one row-counting rule."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Column, Integer, MetaData, Table
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.domain.platform_stats import gaps, integrations, ports


def test_both_repositories_share_count():
    assert gaps.count is integrations.count is ports.count
    assert not hasattr(integrations.IntegrationsRepository, "_count")


@pytest.mark.parametrize(
    ("module", "repository", "method", "keys"),
    [
        (
            gaps,
            gaps.GapRepository,
            "_machine_census",
            (
                "devices",
                "hosted_devices",
                "warm_total",
                "warm_error",
                "host_total",
                "host_enroll_error",
                "host_slots_used",
            ),
        ),
        (
            integrations,
            integrations.IntegrationsRepository,
            "oauth_health",
            ("total", "expired", "expiring_7d", "no_refresh_token"),
        ),
        (
            integrations,
            integrations.IntegrationsRepository,
            "delivery_backlog",
            ("unsent", "dead_letters"),
        ),
    ],
)
async def test_repository_counts_use_shared_rule(
    monkeypatch, module, repository, method, keys
):
    shared_count = AsyncMock(return_value=7)
    monkeypatch.setattr(module, "count", shared_count)
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.all.return_value = []
    result.scalar_one.return_value = None
    session.execute.return_value = result
    session.scalars.return_value = []
    kwargs = {} if method == "_machine_census" else {"now": datetime.now(UTC)}

    snapshot = await getattr(repository(session), method)(**kwargs)

    assert {key: snapshot[key] for key in keys} == dict.fromkeys(keys, 7)
    assert shared_count.await_count == len(keys)
    assert all(call.args[0] is session for call in shared_count.await_args_list)


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ((), 4),
        ((2,), 3),
        ((2, 4), 2),
        ((5,), 0),
    ],
    ids=["all-rows", "one-filter", "two-filters", "no-matches"],
)
async def test_count_rows_with_optional_filters(bounds, expected):
    rows = Table("counted_rows", MetaData(), Column("value", Integer))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(rows.metadata.create_all)
            await connection.execute(rows.insert(), [{"value": n} for n in range(1, 5)])
        async with AsyncSession(engine) as session:
            where = []
            if bounds:
                where.append(rows.c.value >= bounds[0])
            if len(bounds) == 2:
                where.append(rows.c.value < bounds[1])
            result = await ports.count(session, rows, *where)
            assert result == expected
            assert type(result) is int
    finally:
        await engine.dispose()


async def test_count_empty_table_is_zero():
    rows = Table("empty_rows", MetaData(), Column("value", Integer))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(rows.metadata.create_all)
        async with AsyncSession(engine) as session:
            assert await ports.count(session, rows) == 0
    finally:
        await engine.dispose()
