"""Reading a busy key's day of spend does not stop the backend for everyone else.

The end of every turn reads what the project's key has spent today. A busy key
makes tens of thousands of calls a day, and each logged call carries several
kilobytes of metadata: on dev on 2026-10-04 reading the day call by call came
to 238 MB, decoded on the event loop. The day's totals are what is needed.
"""

import asyncio
import gc
import hashlib
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

from app.domain.agent import gateway as gw

pytestmark = pytest.mark.anyio

KEY = "sk-busy"
CALLS = 6000
MODELS = ("deepseek/deepseek-flash", "openai/gpt-6.1-sol")


def _fake_litellm(day: str) -> httpx.MockTransport:
    """A gateway holding one busy day of calls, answering both the spend log
    and the daily totals from them. Every answer is encoded up front, so what
    a read costs here is what the reader does with it."""
    hashed = hashlib.sha256(KEY.encode()).hexdigest()
    metadata = {f"field_{i}": f"value number {i} of this call" for i in range(150)}
    rows = [
        {
            "request_id": f"r{i}",
            "api_key": hashed,
            "model": MODELS[i % 2],
            "prompt_tokens": 1000 + i,
            "completion_tokens": 10,
            "spend": 0.001,
            "metadata": metadata,
        }
        for i in range(CALLS)
    ]
    pages = {
        page: json.dumps({"data": rows[(page - 1) * 1000 : page * 1000]}).encode()
        for page in range(1, CALLS // 1000 + 2)
    }
    totals: dict[str, dict] = {}
    for row in rows:
        t = totals.setdefault(
            row["model"], {"prompt_tokens": 0, "completion_tokens": 0, "spend": 0.0}
        )
        for field in t:
            t[field] += row[field]
    daily = json.dumps(
        {
            "results": [
                {
                    "date": day,
                    "breakdown": {
                        "models": {
                            model: {"api_key_breakdown": {hashed: {"metrics": t}}}
                            for model, t in totals.items()
                        }
                    },
                }
            ]
        }
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        assert query["api_key"] == hashed
        if request.url.path == "/spend/logs/v2":
            body = pages[int(query["page"])]
        elif request.url.path == "/user/daily/activity":
            body = daily
        else:
            return httpx.Response(404)
        return httpx.Response(
            200, content=body, headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handler)


async def _worst_stall_during(work):
    """Run ``work`` and say the longest the loop went without a turn meanwhile.
    The collector is off while it runs: when a full collection lands depends
    on everything else the process holds, not on the read being measured."""
    worst = 0.0
    finished = asyncio.Event()

    async def tick():
        nonlocal worst
        while not finished.is_set():
            before = time.perf_counter()
            await asyncio.sleep(0.005)
            worst = max(worst, time.perf_counter() - before - 0.005)

    gc.collect()
    gc.disable()
    ticking = asyncio.create_task(tick())
    await asyncio.sleep(0)
    try:
        result = await work
    finally:
        gc.enable()
        finished.set()
        await ticking
    return result, worst


async def test_a_busy_keys_day_is_read_without_holding_the_loop():
    day = gw.utc_today()
    gateway = gw.LlmGateway("http://gw", "mk", transport=_fake_litellm(day))

    by, worst = await _worst_stall_during(gateway.daily_spend_by_model(KEY, day))

    assert {name: m.prompt_tokens for name, m in by.items()} == {
        MODELS[0]: sum(1000 + i for i in range(0, CALLS, 2)),
        MODELS[1]: sum(1000 + i for i in range(1, CALLS, 2)),
    }
    assert sum(m.spend_usd for m in by.values()) == pytest.approx(CALLS * 0.001)
    assert worst < 0.03, f"the loop was held {worst * 1000:.0f} ms"
