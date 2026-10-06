"""Proxy usage log → resource_usage + credit deduction, exactly-once.

The metering proxy appends one JSON line per subscription response; these tests
drive ``ingest_once`` against a real file and the real DB: rows land once,
each priced at its model's rates over all four buckets and charged like a
gateway call, a rotated file restarts as a new generation, and unattributable
rows are skipped without wedging the pass."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.feature_stats import pricing
from app.domain.project.services import ProjectService
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService
from app.domain.usage.credits import CREDIT_USD
from app.domain.usage.ledger import Ledger, Rates, payer_for_person
from app.domain.usage.models import ResourceUsage
from app.domain.usage.repositories import UsageRepository
from app.domain.usage.services import UsageService
from app.domain.usage.subscription_ingest import ingest_once
from tests.integration.conftest import registered


async def _seed(factory, credits: float | None = 100.0):
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        if credits is not None:
            await Ledger(session).grant_earmark(
                project_id=project.id, source_task_id=None, credits_total=credits
            )
        pid, tid = project.id, topic.id
        await session.commit()
    return pid, tid


# USD per token: fresh input, output, cache read, 5-minute cache write,
# 1-hour cache write.
OPUS = (5e-6, 25e-6, 5e-7, 6.25e-6, 10e-6)
HAIKU = (1e-6, 5e-6, 1e-7, 1.25e-6, 2e-6)


def _price(monkeypatch, table: dict | None) -> None:
    """The gateway's price table as the ingest reads it; None = unreachable."""

    async def rates(transport=None):
        return table

    monkeypatch.setattr(pricing, "model_rates", rates)


async def _rows(factory, pid) -> list[ResourceUsage]:
    async with factory() as session:
        return list(
            await session.scalars(
                select(ResourceUsage)
                .where(ResourceUsage.project_id == pid)
                .order_by(ResourceUsage.total_tokens)
            )
        )


def _row(
    pid,
    tid,
    *,
    inp=100,
    out=50,
    cache_read=0,
    cache_write=0,
    model=None,
    split: tuple[int, int] | None = None,
    ts: float = 1786000000.0,
):
    """One proxy log line; ``split`` is its (5-minute, 1-hour) cache writes,
    absent on lines the proxy wrote before it logged the split."""
    total = inp + out + cache_read + cache_write
    extra = {}
    if split is not None:
        extra = {
            "cache_creation_5m_input_tokens": split[0],
            "cache_creation_1h_input_tokens": split[1],
        }
    return (
        json.dumps(
            extra
            | {
                "ts": ts,
                "project_id": str(pid),
                "topic_id": str(tid),
                "model": model or "claude-opus-5",
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
                "total_tokens": total,
                "provider": "subscription",
            }
        )
        + "\n"
    )


@pytest.mark.anyio
async def test_a_row_records_all_four_buckets_and_a_cost_once(
    business_db_factory, tmp_path, monkeypatch
):
    _price(monkeypatch, {"claude-opus-5": OPUS})
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid, inp=100, out=50, cache_read=9850, cache_write=40))

    first = await ingest_once(business_db_factory, log)
    again = await ingest_once(business_db_factory, log)

    assert first == {"landed": 1, "skipped": 0}
    assert again == {"landed": 0, "skipped": 0}  # checkpoint: exactly-once
    [row] = await _rows(business_db_factory, pid)
    # No split on the line: its writes are priced at the 1-hour rate.
    cost = 100 * OPUS[0] + 50 * OPUS[1] + 9850 * OPUS[2] + 40 * OPUS[4]
    assert row.route == "subscription"
    # Input counts every prompt token; the cache shares are kept beside it.
    assert (row.input_tokens, row.output_tokens) == (9990, 50)
    assert (row.cache_read_tokens, row.cache_write_tokens) == (9850, 40)
    assert row.cost_usd == pytest.approx(cost)
    assert row.credits == pytest.approx(cost / CREDIT_USD)
    async with business_db_factory() as session:
        balance = await UsageService(session).project_credits(pid)
    assert balance["credits_used"] == pytest.approx(cost / CREDIT_USD)


@pytest.mark.anyio
async def test_cache_reads_are_charged_at_the_cache_read_rate(
    business_db_factory, tmp_path, monkeypatch
):
    _price(monkeypatch, {"claude-opus-5": OPUS})
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(
        _row(pid, tid, inp=10_000, out=0)
        + _row(pid, tid, inp=1, out=0, cache_read=10_000)
    )

    await ingest_once(business_db_factory, log)

    fresh, cached = await _rows(business_db_factory, pid)
    assert fresh.credits == pytest.approx(10_000 * OPUS[0] / CREDIT_USD)
    assert cached.credits == pytest.approx(
        (1 * OPUS[0] + 10_000 * OPUS[2]) / CREDIT_USD
    )


@pytest.mark.anyio
async def test_cache_writes_are_charged_by_their_lifetime(
    business_db_factory, tmp_path, monkeypatch
):
    _price(monkeypatch, {"claude-opus-5": OPUS})
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(
        # 1-hour writes only; both lifetimes; a line written before the split.
        _row(pid, tid, inp=0, out=0, cache_write=1000, split=(0, 1000))
        + _row(pid, tid, inp=0, out=0, cache_write=2000, split=(1500, 500))
        + _row(pid, tid, inp=0, out=0, cache_write=3000)
    )

    await ingest_once(business_db_factory, log)

    hour, both, unsplit = await _rows(business_db_factory, pid)
    assert hour.cost_usd == pytest.approx(1000 * OPUS[4])
    assert both.cost_usd == pytest.approx(1500 * OPUS[3] + 500 * OPUS[4])
    assert unsplit.cost_usd == pytest.approx(3000 * OPUS[4])
    assert (both.cache_write_tokens, both.cache_write_1h_tokens) == (2000, 500)


@pytest.mark.anyio
async def test_same_tokens_cost_the_same_credits_on_gateway_and_subscription(
    business_db_factory, tmp_path, monkeypatch
):
    _price(monkeypatch, {"claude-opus-5": OPUS})
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(
        _row(
            pid,
            tid,
            inp=300,
            out=200,
            cache_read=5000,
            cache_write=700,
            split=(700, 0),
        )
    )
    await ingest_once(business_db_factory, log)

    async with business_db_factory() as session:
        uid = await registered(session, "u")
        await Ledger(session).charge_priced(
            await payer_for_person(session, uid),
            user_id=uid,
            model="claude-opus-5",
            rates=Rates(*OPUS),
            input_tokens=300 + 5000 + 700,
            output_tokens=200,
            cache_read_tokens=5000,
            cache_write_tokens=700,
            kind="assistant",
        )
        await session.commit()
        rows = {
            r.route: r
            for r in await session.scalars(
                select(ResourceUsage).where(ResourceUsage.model == "claude-opus-5")
            )
        }

    assert rows["subscription"].credits > 0
    assert rows["subscription"].credits == pytest.approx(rows["gateway"].credits)
    assert rows["subscription"].cost_usd == pytest.approx(rows["gateway"].cost_usd)


@pytest.mark.anyio
async def test_a_dated_snapshot_is_priced_as_its_model(
    business_db_factory, tmp_path, monkeypatch
):
    _price(monkeypatch, {"claude-haiku-4-5": HAIKU})
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid, inp=1000, out=0, model="claude-haiku-4-5-20251001"))

    await ingest_once(business_db_factory, log)

    [row] = await _rows(business_db_factory, pid)
    assert row.cost_usd == pytest.approx(1000 * HAIKU[0])


@pytest.mark.anyio
async def test_an_unpriced_model_still_records_its_usage(
    business_db_factory, tmp_path, monkeypatch
):
    _price(monkeypatch, {"claude-opus-5": OPUS})
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid, inp=70, out=30, model="claude-unknown-9"))

    assert await ingest_once(business_db_factory, log) == {"landed": 1, "skipped": 0}

    [row] = await _rows(business_db_factory, pid)
    assert (row.model, row.total_tokens) == ("claude-unknown-9", 100)
    assert row.cost_usd == 0.0
    async with business_db_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["unpriced_tokens"] == 100


@pytest.mark.anyio
async def test_an_unreachable_price_table_defers_the_pass_instead_of_free_usage(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway:4000")
    _price(monkeypatch, None)
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid))

    assert await ingest_once(business_db_factory, log) == {"landed": 0, "skipped": 0}
    assert await _rows(business_db_factory, pid) == []

    _price(monkeypatch, {"claude-opus-5": OPUS})
    assert await ingest_once(business_db_factory, log) == {"landed": 1, "skipped": 0}
    [row] = await _rows(business_db_factory, pid)
    assert row.cost_usd > 0


@pytest.mark.anyio
async def test_appended_lines_ingest_incrementally(business_db_factory, tmp_path):
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid))
    await ingest_once(business_db_factory, log)

    with log.open("a") as fh:
        fh.write(_row(pid, tid, inp=7, out=3))
    result = await ingest_once(business_db_factory, log)

    assert result["landed"] == 1
    async with business_db_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["turns"] == 2


@pytest.mark.anyio
async def test_rotated_file_is_a_new_generation(business_db_factory, tmp_path):
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid, inp=11, out=0))
    await ingest_once(business_db_factory, log)

    # Replace the file wholesale (rotation): its rows are NEW spend.
    log.write_text(_row(pid, tid, inp=13, out=0))
    result = await ingest_once(business_db_factory, log)

    assert result["landed"] == 1
    async with business_db_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["input_tokens"] == 24  # 11 + 13, nothing skipped or doubled


@pytest.mark.anyio
async def test_unattributable_rows_are_skipped_not_wedged_on(
    business_db_factory, tmp_path
):
    pid, tid = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    orphan = json.dumps({"project_id": None, "total_tokens": 5}) + "\n"
    ghost = _row("00000000-0000-0000-0000-000000000000", tid)
    log.write_text(orphan + ghost + _row(pid, tid))

    result = await ingest_once(business_db_factory, log)

    assert result == {"landed": 1, "skipped": 2}
    async with business_db_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["turns"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("missing", [True, False])
async def test_invalid_topic_keeps_project_usage_and_advances_once(
    business_db_factory, tmp_path, missing
):
    pid, tid = await _seed(business_db_factory)
    if missing:
        invalid_topic = uuid.uuid4()
    else:
        _, invalid_topic = await _seed(business_db_factory)
    log = tmp_path / "usage.jsonl"
    original = _row(pid, invalid_topic, inp=17, out=3) + _row(pid, tid, inp=7, out=2)
    log.write_text(original)
    assert await ingest_once(business_db_factory, log) == {"landed": 2, "skipped": 0}
    assert await ingest_once(business_db_factory, log) == {"landed": 0, "skipped": 0}
    assert log.read_text() == original
    async with business_db_factory() as session:
        rows = list(
            await session.scalars(
                select(ResourceUsage).where(ResourceUsage.project_id == pid)
            )
        )
        assert {(row.conversation_id, row.total_tokens) for row in rows} == {
            (None, 20),
            (tid, 9),
        }


@pytest.mark.anyio
async def test_a_task_conversation_keeps_its_usage(business_db_factory, tmp_path):
    """A seat working a task reports the task's conversation; its spend is the
    task's, not left unattributed on the project."""
    pid, tid = await _seed(business_db_factory)
    async with business_db_factory() as session:
        task = await TaskService(session).open_thread(
            project_id=pid, room_id=tid, title="t", owner_handle="u", created_by="u"
        )
        await session.commit()
        task_id = task.id
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, task_id, inp=11, out=4))
    assert await ingest_once(business_db_factory, log) == {"landed": 1, "skipped": 0}
    assert [
        (r.conversation_id, r.total_tokens)
        for r in await _rows(business_db_factory, pid)
    ] == [(task_id, 15)]


@pytest.mark.anyio
async def test_a_call_made_during_a_tasks_turn_records_that_turn(
    business_db_factory, tmp_path
):
    """Two turns run one after the other in a task; each model call the seat
    made is booked to the task's conversation and to the turn it was made in."""
    pid, tid = await _seed(business_db_factory)
    first_at = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
    second_at = first_at + timedelta(minutes=10)
    first, second = uuid.uuid4(), uuid.uuid4()
    async with business_db_factory() as session:
        task = await TaskService(session).open_thread(
            project_id=pid, room_id=tid, title="t", owner_handle="u", created_by="u"
        )
        for turn, at in ((first, first_at), (second, second_at)):
            await BlockRepository(session).add(
                project_id=pid,
                conversation_id=task.id,
                author="u",
                author_type=AuthorType.participant,
                content="go",
                turn_id=turn,
                created_at=at,
            )
        await session.commit()
        task_id = task.id
    during_first = (first_at + timedelta(minutes=2)).timestamp()
    during_second = (second_at + timedelta(minutes=3)).timestamp()
    log = tmp_path / "usage.jsonl"
    log.write_text(
        _row(pid, task_id, inp=11, out=4, ts=during_first)
        + _row(pid, task_id, inp=21, out=4, ts=during_second)
    )
    assert await ingest_once(business_db_factory, log) == {"landed": 2, "skipped": 0}
    assert [
        (r.conversation_id, r.turn_id, r.total_tokens)
        for r in await _rows(business_db_factory, pid)
    ] == [(task_id, first, 15), (task_id, second, 25)]
