"""A shared browser, behind one HTTP endpoint.

One browser for the machine, not one per topic. Measured: a shared instance
served six concurrent pages in 4.7 s (1.29 pages/second) where starting a
browser per caller took 9.5 s for three pages — and Chrome is 185 MB installed
once here instead of once per topic home.

It is a separate service rather than a library inside the API for a plain
operational reason: it supervises a browser process tree. An API worker that
owned one could not restart it without restarting request handling, and a
wedged browser would take the API down with it.

The service is READ ONLY by construction — it navigates and returns markup, and
exposes no way to click, submit, or run caller-supplied JavaScript. That matters
before any credential is ever attached to it: a page can carry instructions
aimed at whoever is reading it, and a browser that can only read cannot be
talked into acting.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

#: A Cloudflare interstitial answers immediately and resolves seconds later.
#: Returning at DOMContentLoaded reads the interstitial and reports the page as
#: unreachable — measured, a 1.6 s "failure" became a real page once the wait ran
#: to network idle plus a settle delay.
DEFAULT_SETTLE_SECONDS = 8.0
PAGE_TIMEOUT_MS = 70_000

#: One browser, bounded concurrency. Six was measured healthy; the cap exists so
#: a burst queues instead of spawning contexts until the box swaps.
MAX_CONCURRENT = int(os.environ.get("RENDER_MAX_CONCURRENT", "6"))

_crawler = None
_slots: asyncio.Semaphore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _crawler, _slots
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    _slots = asyncio.Semaphore(MAX_CONCURRENT)
    _crawler = AsyncWebCrawler(
        config=BrowserConfig(
            headless=True,
            verbose=False,
            # The machine's egress, if it has one. Playwright does NOT read
            # HTTPS_PROXY on its own — without this, a proxied host silently
            # fails to reach most of the web and the browser looks useless.
            proxy=os.environ.get("HTTPS_PROXY") or None,
            extra_args=[
                "--ignore-certificate-errors",
                "--disable-gpu",
                "--no-sandbox",
            ],
        )
    )
    await _crawler.__aenter__()
    try:
        yield
    finally:
        await _crawler.__aexit__(None, None, None)


app = FastAPI(lifespan=lifespan, title="cheese browser-render")


class RenderIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    settle_seconds: float = Field(default=DEFAULT_SETTLE_SECONDS, ge=0, le=30)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": _crawler is not None, "max_concurrent": MAX_CONCURRENT}


@app.post("/render")
async def render(body: RenderIn) -> dict:
    from crawl4ai import CacheMode, CrawlerRunConfig

    assert _crawler is not None and _slots is not None
    async with _slots:
        result = await _crawler.arun(
            url=body.url,
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                page_timeout=PAGE_TIMEOUT_MS,
                wait_until="networkidle",
                delay_before_return_html=body.settle_seconds,
            ),
        )
    if not result.success:
        return {"ok": False, "error": result.error_message or "render failed"}
    return {"ok": True, "markdown": str(result.markdown or ""), "html": result.html or ""}
