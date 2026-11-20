"""Building the docs index does not hold the event loop.

Tokenizing the deployed index is hundreds of milliseconds of Python — 403 ms
measured on the docs site's 1.5 M characters — and it runs in whichever request
first finds the copy older than REFRESH_SECONDS. On the loop that is a stall of
that length every ten minutes; off it, the loop keeps answering while the work
happens.
"""

import asyncio
import time

import httpx

from app.domain.docs_site.retrieval import IndexSource

ROWS = [
    {"title": f"page {i}", "heading": "h", "url": f"/p{i}#h", "text": "采纳 " * 200}
    for i in range(200)
]


async def test_the_loop_keeps_beating_while_the_index_is_built(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=ROWS))
    source = IndexSource("http://frontend/docs/sections.json", transport=transport)

    # Stand-in for the real tokenizer: sleeps where the CPU work is, so the
    # measurement is about which thread the work runs on, not how fast it runs.
    def slow_build(sections):
        time.sleep(0.2)
        return object()

    monkeypatch.setattr(
        "app.domain.docs_site.retrieval.DocsIndex", slow_build, raising=True
    )

    beats: list[float] = []
    done = False

    async def beat():
        while not done:
            started = time.monotonic()
            await asyncio.sleep(0.01)
            beats.append(time.monotonic() - started - 0.01)

    task = asyncio.create_task(beat())
    await asyncio.sleep(0)
    await source.get()
    done = True
    await task

    assert beats, "the beater never ran"
    assert max(beats) < 0.05, f"the loop was held for {max(beats) * 1000:.0f} ms"
