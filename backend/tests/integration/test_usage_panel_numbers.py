"""资源面板说的每个数都要对 (P1-8).

The panel showed three numbers and two were wrong: a 3-turn topic reported 43
轮次 (it counted table rows — the metering proxy writes one per /v1/messages
call, the gateway lands a deferred backfill row for the same turn), and 2.28M
subscription tokens reported $0.0000 (a subscription has no per-token price, so
"unknown" was printed as "zero").

These tests drive the real aggregation and the real HTTP endpoints.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.usage.repositories import UsageRepository
from app.domain.usage.subscription_ingest import ingest_once


async def _seed(factory) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        pid, tid = project.id, topic.id
        await session.commit()
    return pid, tid


async def _run_turn(factory, pid, tid, turn_id, started_at: datetime) -> None:
    """A turn as the platform records it: at least one block carrying its id.
    That is what lets proxy traffic be attributed back to a turn.

    ``started_at`` is stamped onto the block because these tests replay turns
    that happened minutes apart — inserting three blocks in the same millisecond
    would make every proxy line look like it belongs to the last one.
    """
    async with factory() as session:
        block = await BlockRepository(session).add(
            project_id=pid,
            topic_id=tid,
            author="u",
            author_type=AuthorType.human,
            content="做点事",
            kind=BlockKind.message,
            turn_id=turn_id,
        )
        await session.execute(
            update(Block).where(Block.id == block.id).values(created_at=started_at)
        )
        await session.commit()


def _proxy_line(pid, tid, *, ts: float, inp=1000, out=200, cache_read=0) -> str:
    return (
        json.dumps(
            {
                "ts": ts,
                "project_id": str(pid),
                "topic_id": str(tid),
                "model": "claude-opus-5",
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": 0,
                "total_tokens": inp + out + cache_read,
                "provider": "subscription",
            }
        )
        + "\n"
    )


@pytest.mark.anyio
async def test_turns_counts_turns_not_rows(client):
    """Three turns' worth of rows — including a deferred gateway backfill for
    one of them — must read as three turns, not five."""
    pid, tid = await _seed(client.test_factory)
    turns = [uuid.uuid4() for _ in range(3)]
    async with client.test_factory() as session:
        repo = UsageRepository(session)
        for turn in turns:
            await repo.add(
                project_id=pid,
                topic_id=tid,
                model="m",
                input_tokens=100,
                output_tokens=10,
                cost_usd=0.01,
                route="gateway",
                turn_id=turn,
            )
        # The deferred drain lands late spend for turns already recorded.
        for turn in turns[:2]:
            await repo.add(
                project_id=pid,
                topic_id=tid,
                model="m",
                input_tokens=5,
                output_tokens=1,
                cost_usd=0.001,
                route="gateway",
                turn_id=turn,
            )
        await session.commit()

    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)

    assert agg["turns"] == 3
    # The tokens still add up across every row — grouping must not drop spend.
    assert agg["input_tokens"] == 3 * 100 + 2 * 5


@pytest.mark.anyio
async def test_proxy_lines_collapse_into_the_turn_that_was_running(client, tmp_path):
    """The 3-轮-shows-43 bug, end to end: many proxy lines per turn."""
    pid, tid = await _seed(client.test_factory)
    log = tmp_path / "usage.jsonl"
    lines = []
    first = datetime.now(UTC) - timedelta(hours=1)
    for n in range(3):
        started = first + timedelta(minutes=10 * n)
        await _run_turn(client.test_factory, pid, tid, uuid.uuid4(), started)
        # A turn makes many /v1/messages calls; each writes its own line.
        lines += [
            _proxy_line(pid, tid, ts=(started + timedelta(seconds=i + 1)).timestamp())
            for i in range(14)
        ]
    log.write_text("".join(lines))

    result = await ingest_once(client.test_factory, log)
    assert result["landed"] == 42

    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["turns"] == 3, "42 proxy lines over 3 turns is 3 turns"
    assert agg["total_tokens"] == 42 * 1200


@pytest.mark.anyio
async def test_traffic_outside_any_known_turn_is_not_folded_into_one(client, tmp_path):
    """Unattributable spend counts as its own turn rather than silently joining
    someone else's — the aggregate never invents an attribution."""
    pid, tid = await _seed(client.test_factory)
    log = tmp_path / "usage.jsonl"
    # No blocks were ever written for this topic: nothing says which turn.
    log.write_text(
        _proxy_line(pid, tid, ts=1_786_000_000.0)
        + _proxy_line(pid, tid, ts=1_786_000_060.0)
    )

    await ingest_once(client.test_factory, log)

    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["turns"] == 2


@pytest.mark.anyio
async def test_traffic_long_after_the_last_turn_is_not_glued_onto_it(client, tmp_path):
    """A line logged a day later did not belong to yesterday's turn. Past the
    attribution window the honest answer is "cannot say", not the nearest turn."""
    pid, tid = await _seed(client.test_factory)
    started = datetime.now(UTC) - timedelta(days=2)
    await _run_turn(client.test_factory, pid, tid, uuid.uuid4(), started)
    log = tmp_path / "usage.jsonl"
    log.write_text(
        _proxy_line(pid, tid, ts=(started + timedelta(seconds=5)).timestamp())
        + _proxy_line(pid, tid, ts=(started + timedelta(days=1)).timestamp())
    )

    await ingest_once(client.test_factory, log)

    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    # One attributed row (the turn) + one that names no turn = two.
    assert agg["turns"] == 2


@pytest.mark.anyio
async def test_subscription_tokens_are_reported_as_unpriced_never_as_zero_cost(
    client, tmp_path
):
    """2.28M tokens must not read as $0.0000 spent. The row has no USD price —
    the aggregate says so, so the panel can print 未知."""
    pid, tid = await _seed(client.test_factory)
    started = datetime.now(UTC) - timedelta(minutes=5)
    await _run_turn(client.test_factory, pid, tid, uuid.uuid4(), started)
    log = tmp_path / "usage.jsonl"
    log.write_text(
        _proxy_line(
            pid,
            tid,
            ts=(started + timedelta(seconds=30)).timestamp(),
            inp=80_000,
            out=200_000,
            cache_read=2_000_000,
        )
    )

    await ingest_once(client.test_factory, log)

    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["total_tokens"] == 2_280_000
    assert agg["cost_usd"] == 0.0
    # …and the aggregate carries the fact that makes that zero readable.
    assert agg["unpriced_tokens"] == 2_280_000


@pytest.mark.anyio
async def test_priced_turns_report_no_unpriced_tokens(client):
    """A gateway turn has a real price; nothing about it is unknown."""
    pid, tid = await _seed(client.test_factory)
    async with client.test_factory() as session:
        await UsageRepository(session).add(
            project_id=pid,
            topic_id=tid,
            model="m",
            input_tokens=1000,
            output_tokens=100,
            cost_usd=0.42,
            route="gateway",
            turn_id=uuid.uuid4(),
        )
        await session.commit()

    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["cost_usd"] == pytest.approx(0.42)
    assert agg["unpriced_tokens"] == 0


@pytest.mark.anyio
async def test_usage_endpoints_expose_turns_and_unpriced_tokens(client):
    """Both panel rows (本话题 / 全项目) carry the same honest fields."""
    pid, tid = await _seed(client.test_factory)
    turn = uuid.uuid4()
    async with client.test_factory() as session:
        repo = UsageRepository(session)
        for _ in range(2):  # two rows, ONE turn
            await repo.add(
                project_id=pid,
                topic_id=tid,
                model="m",
                input_tokens=10,
                output_tokens=1,
                cost_usd=0.0,
                route="subscription",
                turn_id=turn,
            )
        await session.commit()

    topic_usage = client.get(f"/api/topics/{tid}/usage").json()["data"]
    project_usage = client.get(f"/api/projects/{pid}/usage").json()["data"]

    for stats in (topic_usage, project_usage):
        assert stats["turns"] == 1
        assert stats["unpriced_tokens"] == 22
        assert stats["cost_usd"] == 0.0
