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

It reaches the public internet only: every connection goes through the proxy in
``egress.py``, so a page cannot redirect or navigate the browser into the
network this service runs in.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

import egress

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
_gate_port: int | None = None
#: `/inspect` drives Playwright directly: it needs the page itself (viewport,
#: screenshot, a measurement) and crawl4ai hands back only what it extracted.
#: Launched on the first inspection, so a box that never checks a page pays
#: nothing for it.
_playwright = None
_inspector = None
_inspector_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _crawler, _slots
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    _slots = asyncio.Semaphore(MAX_CONCURRENT)
    # Every connection Chrome makes goes through `egress`, which reaches public
    # addresses only (and the machine's own egress proxy, if it has one).
    gate = await egress.start()
    global _gate_port
    _gate_port = gate.sockets[0].getsockname()[1]
    _crawler = AsyncWebCrawler(
        config=BrowserConfig(
            headless=True,
            verbose=False,
            proxy=f"http://127.0.0.1:{gate.sockets[0].getsockname()[1]}",
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
        if _inspector is not None:
            await _inspector.close()
        if _playwright is not None:
            await _playwright.stop()
        gate.close()


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
    return {
        "ok": True,
        "markdown": str(result.markdown or ""),
        "html": result.html or "",
    }


#: Our own measurement, never the caller's: the service still runs no
#: caller-supplied script beyond the page's own. An element counts as pushing
#: the page wider only when nothing above it clips or scrolls it sideways, and
#: only the outermost such element is named — its children say nothing new.
MEASURE_JS = r"""
() => {
  const vw = document.documentElement.clientWidth;
  const clips = (el) => {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const o = getComputedStyle(a).overflowX;
      if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true;
    }
    return false;
  };
  const name = (el) => {
    let n = el.tagName.toLowerCase();
    if (el.id) n += "#" + el.id;
    const cls = [...el.classList].slice(0, 2).join(".");
    if (cls) n += "." + cls;
    const text = (el.innerText || "").trim().replace(/\s+/g, " ").slice(0, 24);
    return text ? n + " 「" + text + "」" : n;
  };
  const wide = [];
  for (const el of document.body.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.right <= vw + 1) continue;
    if (clips(el)) continue;
    if (wide.some((w) => w.el.contains(el))) continue;
    wide.push({ el, right: Math.round(r.right), width: Math.round(r.width) });
    if (wide.length >= 8) break;
  }
  // The page arrives as markup, not from a URL, so a sibling file it names
  // never even becomes a request: an image that did not load is how it shows.
  const broken = [...document.images]
    .filter((img) => img.complete && img.naturalWidth === 0)
    .map((img) => (img.getAttribute("src") || "").slice(0, 200))
    .slice(0, 10);
  // A box that hides its overflow and holds more than it shows: a slide whose
  // text ran past the stage is cut off silently, which no width check sees.
  const clipped = [];
  for (const el of document.body.querySelectorAll("*")) {
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden") continue;
    if (!/hidden|clip/.test(st.overflowY + st.overflowX)) continue;
    // A screen-reader-only label is a 1px box on purpose.
    if (el.clientWidth <= 2 || el.clientHeight <= 2) continue;
    const lost = Math.max(
      el.scrollHeight - el.clientHeight,
      el.scrollWidth - el.clientWidth,
    );
    if (lost <= 4 || !(el.innerText || "").trim()) continue;
    if (clipped.some((c) => c.el.contains(el))) continue;
    clipped.push({ el, lost });
    if (clipped.length >= 8) break;
  }
  return {
    viewport_width: vw,
    broken_images: broken,
    clipped: clipped.map((c) => ({ element: name(c.el), hidden_px: c.lost })),
    page_width: document.scrollingElement.scrollWidth,
    page_height: document.scrollingElement.scrollHeight,
    overflowing: wide.map((w) => ({
      element: name(w.el),
      right: w.right,
      width: w.width,
    })),
  };
}
"""

#: A screenshot taller than this is cut: a 40-screen page is not looked at
#: whole, and the PNG would dwarf everything else in the answer.
MAX_SHOT_HEIGHT = 8000
INSPECT_TIMEOUT_MS = 20_000


class InspectIn(BaseModel):
    html: str = Field(min_length=1, max_length=5_000_000)
    widths: list[int] = Field(default=[400, 1280], min_length=1, max_length=3)
    settle_seconds: float = Field(default=0.8, ge=0, le=10)
    color_scheme: str = Field(default="light", pattern="^(light|dark)$")
    #: Also print it to an A4 PDF with these margins (CSS lengths: one for all
    #: sides, or vertical then horizontal). The page is the layout: printed
    #: here it matches what the room shows, fonts included.
    pdf_margin: str | None = Field(
        default=None, pattern=r"^[0-9.]+(mm|cm|in|px)?( [0-9.]+(mm|cm|in|px)?)?$"
    )


async def _print_pdf(browser, body: InspectIn) -> str:
    parts = (body.pdf_margin or "0").split()
    vertical, horizontal = parts[0], parts[-1]
    context = await browser.new_context(color_scheme="light")
    try:
        page = await context.new_page()
        await page.set_content(body.html, wait_until="load", timeout=INSPECT_TIMEOUT_MS)
        await page.emulate_media(media="print")
        await page.wait_for_timeout(int(body.settle_seconds * 1000))
        pdf = await page.pdf(
            format="A4",
            print_background=True,
            margin={
                "top": vertical,
                "bottom": vertical,
                "left": horizontal,
                "right": horizontal,
            },
        )
    finally:
        await context.close()
    return base64.b64encode(pdf).decode("ascii")


async def _browser():
    global _playwright, _inspector
    async with _inspector_lock:
        if _inspector is None or not _inspector.is_connected():
            from playwright.async_api import async_playwright

            if _playwright is None:
                _playwright = await async_playwright().start()
            _inspector = await _playwright.chromium.launch(
                headless=True,
                proxy={"server": f"http://127.0.0.1:{_gate_port}"},
                args=["--ignore-certificate-errors", "--disable-gpu", "--no-sandbox"],
            )
        return _inspector


async def _inspect_at(browser, body: InspectIn, width: int) -> dict:
    context = await browser.new_context(
        viewport={"width": width, "height": 900},
        device_scale_factor=1,
        color_scheme=body.color_scheme,
    )
    console: list[str] = []
    failed: list[str] = []
    try:
        page = await context.new_page()
        page.on(
            "console",
            lambda m: console.append(m.text[:300]) if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: console.append(str(e)[:300]))
        page.on("requestfailed", lambda r: failed.append(r.url[:200]))
        await page.set_content(body.html, wait_until="load", timeout=INSPECT_TIMEOUT_MS)
        await page.wait_for_timeout(int(body.settle_seconds * 1000))
        measured = await page.evaluate(MEASURE_JS)
        height = min(measured["page_height"], MAX_SHOT_HEIGHT)
        shot = await page.screenshot(
            type="png",
            full_page=height == measured["page_height"],
            clip=None
            if height == measured["page_height"]
            else {"x": 0, "y": 0, "width": width, "height": height},
        )
    finally:
        await context.close()
    return {
        "width": width,
        **measured,
        "cut_at": height if height < measured["page_height"] else None,
        "console_errors": console[:10],
        "failed_requests": failed[:10],
        "png_b64": base64.b64encode(shot).decode("ascii"),
    }


@app.post("/inspect")
async def inspect(body: InspectIn) -> dict:
    """Render one page at a few widths and say what a reader would see.

    For an agent checking a page it made before showing it: a full-page
    screenshot per width, how wide the page really is, which elements push it
    wider than the screen, and what the console and the network complained of.
    """
    assert _slots is not None
    started = time.monotonic()
    async with _slots:
        try:
            browser = await _browser()
            views = [await _inspect_at(browser, body, w) for w in body.widths]
            pdf = await _print_pdf(browser, body) if body.pdf_margin else None
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    return {
        "ok": True,
        "seconds": round(time.monotonic() - started, 2),
        "views": views,
        "pdf_b64": pdf,
    }
