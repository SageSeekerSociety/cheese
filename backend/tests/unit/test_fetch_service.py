"""Behaviour of the platform-side fetch service.

The tests that matter here are about what happens when things go wrong, because
the bug this service replaces was a fetch that went wrong silently: a tool call
that neither returned nor errored, holding one turn open for 17 minutes.
"""

import asyncio

import pytest

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


async def test_a_sandbox_token_is_accepted_when_the_room_is_named(monkeypatch):
    """The shipped bug: /fetch resolved the caller without naming a room.

    A sandbox token is SCOPED — it carries the project and topic it was minted
    for — so verifying it needs to know which room the caller claims to speak
    for. The first release passed neither, and every real sandbox call came back
    "Agent credential is invalid or expired". The behaviour tests around it all
    passed, because none of them went through authentication.

    Reading a public page still needs no per-resource permission. The room is
    named for the credential's sake, not the resource's.
    """
    import uuid as _uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.api import auth as auth_mod
    from app.core.errors import AuthenticationRequiredError, ForbiddenError
    from app.core.sandbox_auth import mint_scoped_token

    project, topic = _uuid.uuid4(), _uuid.uuid4()
    monkeypatch.setattr(
        auth_mod,
        "TopicMemberService",
        lambda _session: SimpleNamespace(
            resolve_agent_handle=AsyncMock(side_effect=lambda t: f"cheese-{t.hex[:12]}")
        ),
    )
    monkeypatch.setattr(
        auth_mod,
        "IdentityService",
        lambda _session: SimpleNamespace(is_agent=AsyncMock(return_value=True)),
    )
    monkeypatch.setattr(
        auth_mod,
        "UserRepository",
        lambda _session: SimpleNamespace(
            get_by_id=AsyncMock(return_value=None),
            get_by_username=AsyncMock(return_value=None),
        ),
    )
    token = mint_scoped_token(project_id=str(project), topic_id=str(topic))

    def resolver():
        return auth_mod.ActorResolver(
            session=MagicMock(), bearer=None, cheese_token=token
        )

    # What the endpoint used to do: no room named. Refused — which of the two
    # refusals it is depends on the token's claims, and either one is a 4xx the
    # sandbox cannot get past.
    with pytest.raises((AuthenticationRequiredError, ForbiddenError)):
        await resolver().resolve(fallback_handle=None)

    # What it does now.
    actor = await resolver().resolve(
        fallback_handle=None, topic_id=topic, project_id=project
    )
    assert actor.is_agent and actor.handle.startswith("cheese-")


async def test_distillation_asks_for_the_model_this_deployment_runs(monkeypatch):
    """The model handed to the distiller must be one the gateway serves.

    Shipped broken once: the endpoint asked for `agent_haiku_model`, reasoning
    that a small model is cheaper. That setting is an ALIAS the CLI resolves
    internally — it says what "haiku" should mean for a session — and its
    default named a model this gateway does not serve. The gateway answered
    "Invalid model name", distillation failed, and every prompted fetch quietly
    returned the whole page instead of an answer.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.api.routes import fetch as fetch_route

    seen: dict = {}

    async def capture(url, prompt=None, **kwargs):
        seen.update(kwargs)
        return service.FetchOutcome(url, True, "text", "plain-http", [])

    monkeypatch.setattr(fetch_route, "fetch_url", capture)
    monkeypatch.setattr(fetch_route.settings, "anthropic_base_url", "http://gw")
    monkeypatch.setattr(fetch_route.settings, "anthropic_auth_token", "tok")
    monkeypatch.setattr(fetch_route.settings, "agent_model", "the-model-we-run")
    monkeypatch.setattr(fetch_route.settings, "agent_haiku_model", "an-alias-only")

    await fetch_route.read_url(
        fetch_route.FetchIn(url="https://example.com/x", prompt="q"),
        SimpleNamespace(resolve=AsyncMock()),
    )

    assert seen["distill"][2] == "the-model-we-run"


async def test_a_short_but_real_page_is_an_answer_not_a_failure(monkeypatch):
    """Thin is not empty.

    The bar for "stop climbing the ladder" was also used as the bar for "this
    page is readable", so pages that are genuinely short came back as "could not
    read this page" with their text sitting in hand — measured, example.com
    yields 101 characters of prose and a Zhihu question page 691.

    A challenge interstitial still has to fail, which is what the floor is for.
    """

    async def thin(url, *a, **k):
        return layers.Attempt(
            "plain-http",
            False,
            "A sentence that is long enough to count as prose. " * 6,
            "only 300 chars of prose",
            0.2,
        )

    async def nothing(url, *a, **k):
        return layers.Attempt("markdown-native", False, "", "no markdown edition", 0.1)

    monkeypatch.setattr(layers, "rung_markdown_native", nothing)
    monkeypatch.setattr(layers, "rung_plain_http", thin)
    monkeypatch.setattr(layers, "rung_impersonated", thin)

    outcome = await service.fetch("https://example.com/short")
    assert outcome.ok, "a short page that was actually read is not a failure"
    assert "sentence" in outcome.text


async def test_a_challenge_page_still_fails(monkeypatch):
    """The floor's whole purpose: an interstitial must not pass as content."""

    async def interstitial(url, *a, **k):
        return layers.Attempt("plain-http", False, "Just a moment...", "shell", 0.1)

    monkeypatch.setattr(layers, "rung_markdown_native", interstitial)
    monkeypatch.setattr(layers, "rung_plain_http", interstitial)
    monkeypatch.setattr(layers, "rung_impersonated", interstitial)

    outcome = await service.fetch("https://example.com/blocked")
    assert not outcome.ok
    assert "plain-http" in outcome.trail()


async def test_a_short_published_markdown_twin_wins_the_first_rung(monkeypatch):
    """`<url>.md` is the site's own copy for machines — length is beside the point.

    The convention (Stripe, Anthropic, our own docs) is that any page URL plus
    `.md` returns the source. Measured, a docs page whose twin is 220 characters
    was rejected by the article-vs-interstitial bar and fell all the way to a
    browser render — the opposite of what this rung exists for. The server
    saying `text/markdown` already answers the question that bar was asking.
    """
    import httpx

    short_markdown = "# Teams\n\nA team is a group that works together on tasks.\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".md"):
            return httpx.Response(
                200, text=short_markdown, headers={"content-type": "text/markdown"}
            )
        return httpx.Response(
            200,
            text="<html><body>rendered</body></html>",
            headers={"content-type": "text/html"},
        )

    original = httpx.AsyncClient

    def with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(layers.httpx, "AsyncClient", with_mock)

    got = await layers.rung_markdown_native("https://example.com/docs/teams")

    assert got.ok, f"a published markdown twin must win here, however short: {got.note}"
    assert "A team is a group" in got.text
    assert got.note.endswith(".md"), "and it must say which URL answered"
