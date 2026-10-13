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
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from curl_cffi import CurlOpt
from curl_cffi import requests as cffi

from app.domain.fetch import guard
from app.domain.fetch.extract import substantive_length, to_markdown
from app.domain.fetch.guard import NotPublic

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


#: A body larger than this is not read further. Claude Code's WebFetch stops at
#: the same 10 MB; a page that size is a dump or a binary, and decoding and
#: converting it costs seconds of the event loop every other request shares.
MAX_BODY_BYTES = 10 * 1024 * 1024


def _is_markdown(content_type: str) -> bool:
    """Text a model reads as it comes: markdown, plain text, and the JSON an API
    answers with (a paper's metadata from a scholarly API is the whole answer)."""
    return any(kind in content_type for kind in ("markdown", "text/plain", "json"))


def _is_page(content_type: str) -> bool:
    """Whether the body is text a converter can read. No content type at all
    is read as HTML, which is what a server that forgets the header serves."""
    return not content_type or "html" in content_type or "xml" in content_type


async def _read_capped(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BODY_BYTES:
            break
    body = b"".join(chunks)[:MAX_BODY_BYTES]
    return body.decode(response.encoding or "utf-8", errors="replace")


async def rung_plain_http(url: str, timeout: float = 15.0) -> Attempt:
    """L0 — one ordinary GET that asks for markdown first, as WebFetch does.

    A site that publishes a markdown edition answers this very request with it
    (``Accept: text/markdown``), and that body is taken whatever its length:
    the server declared it the copy for machines, so a short one is a short
    page. Anything else HTML is converted and graded. Measured, a plain GET
    reads 58% of pages for about a second.

    A body that is neither (a PDF, an image, an archive) is a miss marked
    ``binary`` without being decoded: converting megabytes of binary as if it
    were HTML stalled the event loop, and no rung that renders HTML reads it
    either, so the service skips those rungs.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        async with guard.client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA, "Accept": _MARKDOWN_ACCEPT},
        ) as client:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    return Attempt(
                        "plain-http",
                        False,
                        note=f"HTTP {r.status_code}",
                        seconds=loop.time() - start,
                    )
                ctype = r.headers.get("content-type", "").lower()
                if not _is_markdown(ctype) and not _is_page(ctype):
                    return Attempt(
                        "plain-http",
                        False,
                        note=f"binary {ctype.split(';')[0]}",
                        seconds=loop.time() - start,
                    )
                body = await _read_capped(r)
    except NotPublic:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure here is just a miss
        return Attempt(
            "plain-http",
            False,
            note=f"{type(exc).__name__}",
            seconds=loop.time() - start,
        )
    if _is_markdown(ctype):
        if body.strip():
            rung = "markdown-native" if "markdown" in ctype else "plain-http"
            return Attempt(rung, True, body, url, loop.time() - start)
        return Attempt(
            "plain-http", False, note="empty body", seconds=loop.time() - start
        )
    markdown = await asyncio.to_thread(to_markdown, body)
    return _graded("plain-http", markdown, loop.time() - start)


async def rung_markdown_native(url: str, timeout: float = 8.0) -> Attempt:
    """L1 — the page's ``.md`` twin, for a page whose HTML read thin.

    The convention (Stripe, Anthropic, our own docs) is that a page URL plus
    ``.md`` returns its source; a docs site rendered by JavaScript serves an
    empty shell as HTML and the whole page as ``.md``. One docs page measured
    444 KB as HTML and 3,554 bytes as ``.md``.

    The site's ``llms.txt`` is not asked for. It is an index of the whole site,
    not the page that was asked for, and taking it as the page answered half
    the fetches this rung won with the wrong document: a GitHub repository
    page, a blog post and a 403 page all came back as their site's index.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    parsed = urlparse(url)
    # The twin is the page's PATH plus `.md`, on the same host. Appended to the
    # whole URL instead, a site root became `https://example.com.md` — another
    # host, which the guard refuses as unresolvable, and a refusal there ends
    # the whole fetch. A root has no page name to append to, so it has no twin.
    page = parsed.path.rstrip("/")
    if not (parsed.scheme and parsed.netloc and page):
        return Attempt("markdown-native", False, note="no page name", seconds=0.0)
    twin = urlunparse((parsed.scheme, parsed.netloc, page + ".md", "", "", ""))
    try:
        async with guard.client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA, "Accept": _MARKDOWN_ACCEPT},
        ) as client:
            r = await client.get(twin)
    except NotPublic:
        raise
    except Exception as exc:  # noqa: BLE001
        return Attempt(
            "markdown-native",
            False,
            note=f"{type(exc).__name__}",
            seconds=loop.time() - start,
        )
    # Only when the SERVER says markdown/plain. A site that answers `.md` with
    # its normal HTML page (or its 404 page) has not published a twin.
    if r.status_code == 200 and _is_markdown(r.headers.get("content-type", "")):
        if r.text.strip():
            return Attempt("markdown-native", True, r.text, twin, loop.time() - start)
    return Attempt(
        "markdown-native",
        False,
        note="no markdown edition published",
        seconds=loop.time() - start,
    )


async def rung_impersonated(url: str, timeout: float = 25.0) -> Attempt:
    """L2 — same request, with a real browser's TLS fingerprint.

    Rescues sites that refuse on JA3 alone. It does NOT defeat a Cloudflare
    challenge or a hard 403 — measured, both still refuse — so it sits below the
    browser rung rather than replacing it.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()

    def _get(target: str, address: str) -> tuple[int, str, str | None]:

        host, port = guard._target(target)
        r = cffi.get(
            target,
            impersonate="chrome",
            timeout=timeout,
            # Redirects are followed below, one checked hop at a time, and each
            # connection goes to the address that was checked for its host.
            allow_redirects=False,
            curl_options={CurlOpt.RESOLVE: [f"{host}:{port}:{address}".encode()]},
        )
        location = r.headers.get("location") if 300 <= r.status_code < 400 else None
        return r.status_code, r.text, location

    async def _follow() -> tuple[int, str]:
        target = url
        for _hop in range(guard.MAX_REDIRECTS + 1):
            address = await guard.check(target)
            status, body, location = await asyncio.to_thread(_get, target, address)
            if location is None:
                return status, body
            target = urljoin(target, location)
        return 310, ""

    try:
        status, body = await asyncio.wait_for(_follow(), timeout=timeout + 5)
    except NotPublic:
        raise
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
    markdown = await asyncio.to_thread(to_markdown, body)
    return _graded("impersonated", markdown, loop.time() - start)


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
    markdown = payload.get("markdown") or (
        await asyncio.to_thread(to_markdown, html) if html else ""
    )
    return _graded("browser", markdown, loop.time() - start)
