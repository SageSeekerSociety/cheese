"""Proxy usage log → resource_usage + credit deduction, exactly-once.

The metering proxy appends one JSON line per subscription response; these tests
drive ``ingest_once`` against a real file and the real DB: rows land once,
credits burn at the flat token rate over all four buckets, a rotated file
restarts as a new generation, and unattributable rows are skipped without
wedging the pass."""

import json

import pytest

from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository
from app.domain.usage.subscription_ingest import ingest_once


async def _seed(factory, credits: float | None = 100.0):
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        if credits is not None:
            await ComputeGrantRepository(session).grant(
                project_id=project.id, source_task_id=None, credits_total=credits
            )
        pid, tid = project.id, topic.id
        await session.commit()
    return pid, tid


def _row(pid, tid, *, inp=100, out=50, cache_read=0, cache_write=0):
    total = inp + out + cache_read + cache_write
    return (
        json.dumps(
            {
                "ts": 1786000000.0,
                "project_id": str(pid),
                "topic_id": str(tid),
                "model": "claude-opus-5",
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
async def test_rows_land_once_with_route_and_credits(client, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "compute_credit_tokens", 10_000)
    pid, tid = await _seed(client.test_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid, inp=100, out=50, cache_read=9850))

    first = await ingest_once(client.test_factory, log)
    again = await ingest_once(client.test_factory, log)

    assert first == {"landed": 1, "skipped": 0}
    assert again == {"landed": 0, "skipped": 0}  # checkpoint: exactly-once
    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
        balance = await ComputeGrantRepository(session).summary(pid)
    # Cache reads fold into input; credits burn the full 10k tokens = 1 credit.
    assert (agg["input_tokens"], agg["output_tokens"]) == (9950, 50)
    assert agg["turns"] == 1
    assert balance["credits_used"] == pytest.approx(1.0)


@pytest.mark.anyio
async def test_appended_lines_ingest_incrementally(client, tmp_path):
    pid, tid = await _seed(client.test_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid))
    await ingest_once(client.test_factory, log)

    with log.open("a") as fh:
        fh.write(_row(pid, tid, inp=7, out=3))
    result = await ingest_once(client.test_factory, log)

    assert result["landed"] == 1
    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["turns"] == 2


@pytest.mark.anyio
async def test_rotated_file_is_a_new_generation(client, tmp_path):
    pid, tid = await _seed(client.test_factory)
    log = tmp_path / "usage.jsonl"
    log.write_text(_row(pid, tid, inp=11, out=0))
    await ingest_once(client.test_factory, log)

    # Replace the file wholesale (rotation): its rows are NEW spend.
    log.write_text(_row(pid, tid, inp=13, out=0))
    result = await ingest_once(client.test_factory, log)

    assert result["landed"] == 1
    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["input_tokens"] == 24  # 11 + 13, nothing skipped or doubled


@pytest.mark.anyio
async def test_unattributable_rows_are_skipped_not_wedged_on(client, tmp_path):
    pid, tid = await _seed(client.test_factory)
    log = tmp_path / "usage.jsonl"
    orphan = json.dumps({"project_id": None, "total_tokens": 5}) + "\n"
    ghost = _row("00000000-0000-0000-0000-000000000000", tid)
    log.write_text(orphan + ghost + _row(pid, tid))

    result = await ingest_once(client.test_factory, log)

    assert result == {"landed": 1, "skipped": 2}
    async with client.test_factory() as session:
        agg = await UsageRepository(session).for_topic(tid)
    assert agg["turns"] == 1
