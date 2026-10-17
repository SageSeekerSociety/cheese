"""`conditional_json` hands the event loop back while it encodes a large list.

A 304 still needs the whole body to know its tag, so one `json.dumps` of a big
envelope held the loop for the full encode — 227 ms on the 60,000-row case
(#1825, 2026-10-17) — and every other request waited behind it, sent back or not.
"""

import asyncio
import gc
import hashlib
import itertools
import json

import pytest

from app.api import conditional
from app.core import loop_lag


def _rows(n: int) -> dict:
    """An envelope shaped like the task lists: dicts in insertion order that is not
    sorted, non-ASCII text, nested values, floats, None and booleans."""
    return {
        "ok": True,
        "data": {
            "total": n,
            "items": [
                {
                    "title": f"任务 {i} “quoted” \\ \n",
                    "id": i,
                    "score": i / 7,
                    "owner": None if i % 3 else "符露夀",
                    "flags": [i % 2 == 0, {"z": 1, "a": [i]}],
                }
                for i in range(n)
            ],
        },
    }


def _canonical(data) -> bytes:
    return json.dumps(
        data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


@pytest.mark.anyio
async def test_body_and_etag_are_byte_for_byte_what_json_dumps_gives() -> None:
    data = _rows(60_000)
    expected = _canonical(data)

    response = await conditional.conditional_json(data, None)

    assert response.body == expected
    etag = hashlib.sha256(expected).hexdigest()
    assert response.headers["ETag"] == f'"{etag}"'
    # The other ETag in this module (admin roster) hashes the same canonical form.
    assert conditional.etag_for_json(data) == etag
    hit = await conditional.conditional_json(data, f'W/"{etag}"')
    assert hit.status_code == 304
    assert hit.body == b""


@pytest.mark.anyio
async def test_no_more_than_the_threshold_of_chunks_run_between_two_yields(
    monkeypatch,
) -> None:
    encoder = conditional._CANONICAL_ENCODER
    real = type(encoder).iterencode
    produced = 0

    def counting(data):
        nonlocal produced
        for chunk in real(encoder, data):
            produced += 1
            yield chunk

    monkeypatch.setattr(encoder, "iterencode", counting)

    seen: list[int] = []
    done = False

    async def ticker() -> None:
        while not done:
            seen.append(produced)
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    await conditional.conditional_json(_rows(60_000), None)
    done = True
    await task
    seen.append(produced)

    assert produced > 10 * conditional.YIELD_EVERY_CHUNKS
    gaps = [b - a for a, b in itertools.pairwise(seen)]
    assert max(gaps) <= conditional.YIELD_EVERY_CHUNKS


@pytest.mark.anyio
async def test_the_loop_lag_probe_sees_no_long_stall_while_encoding() -> None:
    data = _rows(60_000)
    loop_lag._worst = 0.0
    loop_lag._recent = 0.0
    # The collector is held off for the measurement: a full collection over the
    # 60,000 rows this test just built is itself ~100 ms on a CI box, and it lands
    # wherever the allocation count tips over. That pause is the heap's, not the
    # encoder's, and would make this test say nothing about the yields.
    gc.collect()
    gc.disable()
    probe = asyncio.create_task(loop_lag.watch_loop_lag(interval_s=0.002, stall_s=10.0))
    try:
        await asyncio.sleep(0.01)
        await conditional.conditional_json(data, None)
        # Let the probe wake once more: a stall is only measured when it ends.
        await asyncio.sleep(0.02)
        worst_ms = loop_lag.lag_status()["worst_ms"]
    finally:
        probe.cancel()
        gc.enable()
        loop_lag._worst = 0.0
        loop_lag._recent = 0.0
    assert worst_ms < 50, worst_ms
