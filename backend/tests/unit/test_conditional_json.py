"""`conditional_json` hands the event loop back while it encodes a large body.

A 304 still needs the whole body to know its tag, so one `json.dumps` of a big
envelope held the loop for the full encode — 227 ms on a 60,000-row list
(#1825, 2026-10-17) — and every other request waited behind it, sent back or not.
A short list whose rows are large stalls the same way: one call of a page of
1,000-block conversations is over 100 ms, and the split has to follow the size
of what is written, not only the number of elements.
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
async def test_no_more_than_the_threshold_of_elements_run_between_two_yields(
    monkeypatch,
) -> None:
    real = conditional._dumps
    encoded = 0

    def counting(value):
        nonlocal encoded
        encoded += 1
        return real(value)

    monkeypatch.setattr(conditional, "_dumps", counting)

    seen: list[int] = []
    done = False

    async def ticker() -> None:
        while not done:
            seen.append(encoded)
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    await conditional.conditional_json(_rows(ROWS), None)
    done = True
    await task
    seen.append(encoded)

    assert encoded > 10 * conditional.YIELD_EVERY_ITEMS
    gaps = [b - a for a, b in itertools.pairwise(seen)]
    # The envelope's own few keys ride along with the first stretch.
    assert max(gaps) <= conditional.YIELD_EVERY_ITEMS + 8


def _page(rows: int, blocks: int) -> dict:
    """A room's task page as `GET /topics/{id}/tasks` answers with no limit: a
    short list of rows, each carrying its whole conversation."""
    return {
        "ok": True,
        "data": {
            "total": rows,
            "items": [
                {
                    "id": f"task-{i}",
                    "title": f"任务 {i}",
                    "blocks": [
                        {"id": j, "content": "x" * 300, "author": "符露夀"}
                        for j in range(blocks)
                    ],
                }
                for i in range(rows)
            ],
        },
    }


@pytest.mark.anyio
async def test_a_short_page_of_large_rows_is_split_too(monkeypatch) -> None:
    """The rows are few (a page, under the split threshold) but each is a whole
    conversation: one `_dumps` of the page is ~7 MB and held the loop for ~78 ms.
    The walk has to follow the size and not just the element count, or the shape
    the condition exists for is the one it misses."""
    data = _page(100, 200)
    real = conditional._dumps
    widest = 0

    def counting(value):
        nonlocal widest
        text = real(value)
        widest = max(widest, len(text))
        return text

    monkeypatch.setattr(conditional, "_dumps", counting)

    response = await conditional.conditional_json(data, None)

    assert response.body == _canonical(data)
    # No call writes a whole page: the largest is a single row. Without the
    # size check the page's items went out in one ~7 MB call.
    assert widest < conditional.WALK_ABOVE_CHARS, widest


@pytest.mark.anyio
async def test_a_short_page_of_large_rows_leaves_the_loop_free() -> None:
    data = _page(100, 200)
    worst = float("inf")
    for _ in range(3):
        worst = min(worst, await _worst_stall_during(data))
    assert worst < 0.05, worst


@pytest.mark.anyio
async def test_a_short_list_is_one_c_encode() -> None:
    """A sidebar's worth of small rows is encoded in one call, not walked in
    Python: the walk is only for lists long or large enough to hold the loop."""
    data = _rows(conditional.SPLIT_LISTS_LONGER_THAN)
    calls = 0
    real = conditional._dumps

    def counting(value):
        nonlocal calls
        calls += 1
        return real(value)

    conditional._dumps = counting
    try:
        response = await conditional.conditional_json(data, None)
    finally:
        conditional._dumps = real
    assert response.body == _canonical(data)
    assert calls < 10


@pytest.mark.anyio
async def test_odd_shapes_come_out_as_json_dumps_writes_them() -> None:
    data = {
        "b": [{"y": 1, "x": [1.5, None, True]}]
        * (conditional.SPLIT_LISTS_LONGER_THAN + 3),
        "a": {2: "int keys", 1: "are converted by json.dumps"},
        "c": ("tu", "ple"),
        "d": {},
        "e": [],
        "\u00e9": "é",
    }
    response = await conditional.conditional_json(data, None)
    assert response.body == _canonical(data)


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
