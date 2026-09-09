"""Behaviour of the platform-side fetch service.

The tests that matter here are about what happens when things go wrong, because
the bug this service replaces was a fetch that went wrong silently: a tool call
that neither returned nor errored, holding one turn open for 17 minutes.
"""

import asyncio

from app.domain.fetch import layers, service
from app.domain.fetch.extract import substantive_length, to_markdown

# A directory page: many short entries, mostly links, very little prose per
# block. This is the shape that readability-style extractors discard wholesale.
LIST_PAGE = """
<html><head><title>Directory</title><style>.x{color:red}</style>
<script>window.__DATA__ = "%s";</script></head>
<body><nav><a href="/a">A</a><a href="/b">B</a></nav>
<main>
  <p>The registry lists tools that agents can install, together with the number
     of installations each one reports and the repository it is published from.</p>
  <ul>
    <li><a href="/1">agent-browser</a> — browser automation for agents</li>
    <li><a href="/2">just-scrape</a> — page extraction without a browser</li>
  </ul>
  <p>Entries are refreshed hourly, and an entry that has not been refreshed in
     more than a day is hidden from the directory until it reports again.</p>
</main></body></html>
""" % ("x" * 5000)


def test_a_list_page_survives_extraction():
    """A page that is mostly links must not come back empty.

    Extractors that score blocks by text density throw this shape away — the
    page IS short entries and links. Measured on a real directory page, that
    scoring returned 622 characters where conversion returned 24,304.
    """
    markdown = to_markdown(LIST_PAGE)
    assert "agent-browser" in markdown
    assert "refreshed hourly" in markdown
    # Script and style contents are never prose and must not reach the model.
    assert "window.__DATA__" not in markdown
    assert "color:red" not in markdown


def test_prose_is_counted_and_link_walls_are_not():
    """Telling a real page from a shell cannot be done by keyword.

    A page carrying real content was once graded as blocked because the word for
    "log in" appeared in its header, so the measure is how much of it reads like
    a sentence.
    """
    links_only = "\n".join(f"[item {i}](https://example.com/{i})" for i in range(200))
    assert substantive_length(links_only) == 0
    assert substantive_length(to_markdown(LIST_PAGE)) > 100


async def test_a_distillation_that_never_answers_does_not_hang_the_fetch(monkeypatch):
    """The whole reason this service exists.

    When the model call made on the extracted text never settles, the fetch must
    still return. Upstream, that step had no deadline and a hang there held a
    turn open for 17 minutes.
    """

    async def never_returns(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_distill", never_returns)
    # Shorten the service's OWN deadline so this asserts that the service gives
    # up, not that the test's patience ran out first.
    monkeypatch.setattr(service, "DISTILL_TIMEOUT_SECONDS", 0.5)

    async def page(url, *a, **k):
        return layers.Attempt("plain-http", True, "prose. " * 500, "", 0.1)

    monkeypatch.setattr(layers, "rung_markdown_native", page)
    monkeypatch.setattr(layers, "rung_plain_http", page)

    outcome = await asyncio.wait_for(
        service.fetch(
            "https://example.com/x",
            "what is this",
            distill=("https://gw", "tok", "haiku"),
        ),
        timeout=10,
    )
    assert outcome.ok, "a stalled distillation must not fail the fetch"
    assert not outcome.distilled
    assert "prose." in outcome.text, "it must fall back to the page itself"


async def test_a_failed_fetch_reports_how_each_rung_refused(monkeypatch):
    """ "Could not read this page" with no trail is unimprovable."""

    async def refuses(url, *a, **k):
        return layers.Attempt("plain-http", False, "", "HTTP 403", 0.2)

    async def no_markdown(url, *a, **k):
        return layers.Attempt("markdown-native", False, "", "no markdown edition", 0.1)

    monkeypatch.setattr(layers, "rung_markdown_native", no_markdown)
    monkeypatch.setattr(layers, "rung_plain_http", refuses)
    monkeypatch.setattr(layers, "rung_impersonated", refuses)

    outcome = await service.fetch("https://example.com/blocked")
    assert not outcome.ok
    trail = outcome.trail()
    assert "markdown-native" in trail and "no markdown edition" in trail
    assert "HTTP 403" in trail


async def test_third_party_and_browser_rungs_stay_off_unless_configured(monkeypatch):
    """Sending a URL to a third party is an operator's decision, not a default.

    The browser rung is likewise opt-in: it costs a Chrome install and seconds
    per page, so an unconfigured deployment must never reach either.
    """
    called: list[str] = []

    async def miss(url, *a, **k):
        return layers.Attempt("plain-http", False, "", "miss", 0.1)

    async def record_reader(url, endpoint, *a, **k):
        called.append("reader")
        return layers.Attempt("reader-service", False, "", "miss", 0.1)

    async def record_browser(url, endpoint, *a, **k):
        called.append("browser")
        return layers.Attempt("browser", False, "", "miss", 0.1)

    monkeypatch.setattr(layers, "rung_markdown_native", miss)
    monkeypatch.setattr(layers, "rung_plain_http", miss)
    monkeypatch.setattr(layers, "rung_impersonated", miss)
    monkeypatch.setattr(layers, "rung_reader_service", record_reader)
    monkeypatch.setattr(layers, "rung_browser", record_browser)

    await service.fetch("https://example.com/x")
    assert called == [], "no endpoint configured, so neither may be contacted"

    await service.fetch(
        "https://example.com/x",
        reader_endpoint="https://r.example",
        browser_endpoint="http://render:8900",
    )
    assert called == ["reader", "browser"], "configured rungs run cheapest-first"
