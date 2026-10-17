"""`conditional_json` hands the event loop back while it encodes a large list.

A 304 still needs the whole body to know its tag, so one `json.dumps` of a big
envelope held the loop for the full encode — 227 ms on a 60,000-row list
(#1825, 2026-10-17) — and every other request waited behind it, sent back or not.
"""

import asyncio
import gc
import hashlib
import itertools
import json
import time

import pytest

from app.api import conditional

#: Enough rows for many yield stretches (about 30 chunks a row) while keeping the
#: file inside the unit layer's time ceiling on a busy CI runner.
ROWS = 10_000


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
    data = _rows(ROWS)
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
    await conditional.conditional_json(_rows(ROWS), None)
    done = True
    await task
    seen.append(produced)

    assert produced > 10 * conditional.YIELD_EVERY_CHUNKS
    gaps = [b - a for a, b in itertools.pairwise(seen)]
    assert max(gaps) <= conditional.YIELD_EVERY_CHUNKS


@pytest.mark.anyio
async def test_no_stretch_between_two_yields_holds_the_loop_for_long() -> None:
    """CPU time, not wall clock: on a loaded runner a wake-up waits for a core while
    nothing runs, and a wall clock would charge that wait to the encoder (the same
    reasoning as ``test_gateway_spend_leaves_the_loop_free``). The least of three
    reads is kept, so one slow core does not decide it.

    Without the yields this list is one stretch of well over 100 ms of CPU; with
    them each stretch is a few milliseconds."""
    data = _rows(ROWS)
    worst = float("inf")
    for _ in range(3):
        worst = min(worst, await _worst_stall_during(data))
    assert worst < 0.05, worst


async def _worst_stall_during(data) -> float:
    """The longest CPU stretch the loop spent away from a ticker while encoding."""
    stall = 0.0
    finished = asyncio.Event()

    async def tick() -> None:
        nonlocal stall
        while not finished.is_set():
            before = time.thread_time()
            await asyncio.sleep(0)
            stall = max(stall, time.thread_time() - before)

    # A full collection over the rows just built lands wherever the allocation
    # count tips over; that pause is the heap's, not the encoder's.
    gc.collect()
    gc.disable()
    ticking = asyncio.create_task(tick())
    await asyncio.sleep(0)
    try:
        await conditional.conditional_json(data, None)
    finally:
        gc.enable()
        finished.set()
        await ticking
    return stall
