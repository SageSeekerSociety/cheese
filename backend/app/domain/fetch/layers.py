"""The fetch ladder: cheap attempts first, expensive ones only after a miss.

No single method reaches most of the web. Measured across 20 real pages, plain
HTTP read 58% of them, and every other method both rescued pages plain HTTP
missed AND missed pages plain HTTP read — Jina returned Stack Overflow (403 to
us) and a blog our egress cannot reach, while failing on two sites plain HTTP
handled; a real browser returned a Zhihu question and a Douban page neither of
the others could. The union of the ladder reached 85%.

So the design is a ladder, not a winner. Each rung states what it costs and what
it is for; the caller stops at the first rung that returns prose.

Every rung is bounded. That is not incidental: the failure this whole service
exists to avoid is a fetch that neither returns nor errors, and the way that
happened upstream was a step with no deadline on it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from app.domain.fetch.extract import substantive_length, to_markdown

#: Enough prose that a rung can stop climbing: the page clearly carried an
#: article. Below it the ladder keeps trying, because a thin result is usually a
#: challenge interstitial or a login wall.
MIN_SUBSTANTIVE = 800

#: But thin is not the same as empty, and some real pages ARE short — a Q&A
#: page with few answers, a minimal example page. Measured: example.com yields
#: 101 characters of prose and a Zhihu question page 691, and both were reported
#: as "could not read this page" while their text sat in hand. So once every
#: rung has been tried, anything above this floor is returned as the answer
#: rather than thrown away; only below it is a page genuinely unreadable.
MIN_USABLE = 60

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_MARKDOWN_ACCEPT = "text/markdown, text/plain;q=0.9, text/html;q=0.8, */*;q=0.1"


@dataclass(frozen=True)
class Attempt:
    """One rung's outcome. ``text`` is markdown when ``ok``; ``note`` says why not."""

    rung: str
    ok: bool
    text: str = ""
    note: str = ""
    seconds: float = 0.0

    @property
    def substantive(self) -> int:
        return substantive_length(self.text) if self.text else 0


def _graded(rung: str, text: str, seconds: float, *, source: str = "") -> Attempt:
    body = substantive_length(text)
    if body >= MIN_SUBSTANTIVE:
        return Attempt(rung, True, text, source, seconds)
    return Attempt(rung, False, text, f"only {body} chars of prose", seconds)


async def rung_markdown_native(url: str, timeout: float = 12.0) -> Attempt:
    """L0 — ask the site for markdown it already publishes for machines.

    Three shapes, all cheap and all exact: ``Accept: text/markdown``, the same
    URL with ``.md`` appended, and the nearest ``llms.txt`` walking up the path.
    When a site answers, this beats every other rung by an order of magnitude on
    both quality and cost — one docs page measured 444 KB as HTML and 3,554
    bytes as ``.md``, the same content without the chrome.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    parsed = urlparse(url)
    candidates: list[tuple[str, dict[str, str]]] = [
        (url, {"Accept": _MARKDOWN_ACCEPT}),
        (url.rstrip("/") + ".md", {"Accept": _MARKDOWN_ACCEPT}),
    ]
    if parsed.scheme and parsed.netloc:
        candidates.append(
            (urljoin(f"{parsed.scheme}://{parsed.netloc}", "/llms.txt"), {})
        )
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": _BROWSER_UA}
    ) as client:
        for candidate, headers in candidates:
            try:
                r = await client.get(candidate, headers=headers)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            ctype = r.headers.get("content-type", "")
            body = r.text
            # Only accept it when the SERVER says markdown/plain. A site that
            # answers `.md` with its normal HTML page has not published a
            # markdown edition, and taking it here would skip the converter for
            # no gain.
            if "markdown" not in ctype and "text/plain" not in ctype:
                continue
            if substantive_length(body) >= MIN_SUBSTANTIVE:
                return Attempt(
                    "markdown-native", True, body, candidate, loop.time() - start
                )
    return Attempt(
        "markdown-native",
        False,
        note="no markdown edition published",
        seconds=loop.time() - start,
    )


async def rung_plain_http(url: str, timeout: float = 20.0) -> Attempt:
    """L1 — an ordinary HTTPS GET. Reads 58% of pages for ~1 s and no dependency."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA, "Accept": _MARKDOWN_ACCEPT},
        ) as client:
            r = await client.get(url)
    except Exception as exc:  # noqa: BLE001 — every failure here is just a miss
        return Attempt(
            "plain-http",
            False,
            note=f"{type(exc).__name__}",
            seconds=loop.time() - start,
        )
    if r.status_code != 200:
        return Attempt(
            "plain-http",
            False,
            note=f"HTTP {r.status_code}",
            seconds=loop.time() - start,
        )
    return _graded("plain-http", to_markdown(r.text), loop.time() - start)


async def rung_impersonated(url: str, timeout: float = 25.0) -> Attempt:
    """L2 — same request, with a real browser's TLS fingerprint.

    Rescues sites that refuse on JA3 alone. It does NOT defeat a Cloudflare
    challenge or a hard 403 — measured, both still refuse — so it sits below the
    browser rung rather than replacing it.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()

    def _get() -> tuple[int, str]:
        from curl_cffi import requests as cffi

        r = cffi.get(url, impersonate="chrome", timeout=timeout)
        return r.status_code, r.text

    try:
        status, body = await asyncio.wait_for(
            asyncio.to_thread(_get), timeout=timeout + 5
        )
    except Exception as exc:  # noqa: BLE001
        return Attempt(
            "impersonated",
            False,
            note=f"{type(exc).__name__}",
            seconds=loop.time() - start,
        )
    if status != 200:
        return Attempt(
            "impersonated", False, note=f"HTTP {status}", seconds=loop.time() - start
        )
    return _graded("impersonated", to_markdown(body), loop.time() - start)


async def rung_reader_service(
    url: str, endpoint: str, timeout: float = 40.0
) -> Attempt:
    """L3 — a third-party reader, for pages our own egress cannot reach.

    Its value is an egress our datacentre IP does not have; measured, it returned
    a Cloudflare-challenged page and a host our network could not connect to at
    all. Two things follow. It is OPTIONAL, because using it hands the URL to a
    third party — the caller decides. And it is rate-limited upstream: hammering
    it degrades into short truncated bodies that look exactly like "the page is
    unreadable", so a short body here is reported as a miss with the reason, not
    as the page being blocked.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(endpoint.rstrip("/") + "/" + url)
    except Exception as exc:  # noqa: BLE001
        return Attempt(
            "reader-service",
            False,
            note=f"{type(exc).__name__}",
            seconds=loop.time() - start,
        )
    if r.status_code != 200:
        return Attempt(
            "reader-service",
            False,
            note=f"HTTP {r.status_code}",
            seconds=loop.time() - start,
        )
    return _graded("reader-service", r.text, loop.time() - start)


#: A Cloudflare interstitial answers fast and then resolves slowly; returning at
#: DOMContentLoaded reads the interstitial and calls the page unreachable.
#: Measured: 1.6 s "failure" became a real page once the wait ran to network
#: idle plus a settle delay.
BROWSER_SETTLE_SECONDS = 8.0


async def rung_browser(url: str, endpoint: str, timeout: float = 75.0) -> Attempt:
    """L4 — a shared, platform-side browser.

    Not one browser per sandbox: measured, a shared instance served six
    concurrent pages in 4.7 s (1.29 pages/second) against 9.5 s for three pages
    when each caller started its own, and it keeps Chrome to one 185 MB install
    for the machine instead of one per topic.

    The browser lives behind an HTTP endpoint rather than inside this process:
    it needs Chrome, it needs to be pooled, and giving the API process a
    headless browser to supervise would couple a request path to a subprocess
    tree it cannot restart cleanly.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                endpoint.rstrip("/") + "/render",
                json={"url": url, "settle_seconds": BROWSER_SETTLE_SECONDS},
            )
    except Exception as exc:  # noqa: BLE001
        return Attempt(
            "browser", False, note=f"{type(exc).__name__}", seconds=loop.time() - start
        )
    if r.status_code != 200:
        return Attempt(
            "browser", False, note=f"HTTP {r.status_code}", seconds=loop.time() - start
        )
    payload = r.json()
    html = payload.get("html") or ""
    markdown = payload.get("markdown") or (to_markdown(html) if html else "")
    return _graded("browser", markdown, loop.time() - start)
