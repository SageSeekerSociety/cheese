"""Search the web for an agent, through a search service the operator runs.

Codex and pi have no search tool at all, and Claude Code's WebSearch is a tool
the model provider executes: on a model that is not Claude it fails
(``Unsupported tool type: web_search_preview``). Agents in those sessions were
opening search engines' result pages with ``cheese_fetch`` instead — measured,
153 fetches of Bing, Google, Baidu and Google Scholar result URLs in two weeks,
the last of which answer a crawler with a block page.

The service is reached over HTTP and speaks SearXNG's JSON API
(``GET /search?q=…&format=json``), which a self-hosted SearXNG answers without
any account or key. Like the fetch service's reader and browser, it is off
until an operator configures it: which engines a deployment queries, and from
which network, is that operator's decision.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

#: A search answers in a second or two; past this the engines it fans out to
#: are not answering, and the caller is better told so than kept waiting.
SEARCH_TIMEOUT_SECONDS = 15.0


class SearchUnavailable(Exception):
    """The search service could not be asked, or did not answer."""


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str


async def search(query: str, endpoint: str, *, limit: int = 8) -> list[Result]:
    """The first ``limit`` results for ``query``, best first."""
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            r = await client.get(
                endpoint.rstrip("/") + "/search",
                params={"q": query, "format": "json"},
            )
    except httpx.HTTPError as exc:
        raise SearchUnavailable(type(exc).__name__) from exc
    if r.status_code != 200:
        # i18n-exempt: a status code; the route words it through say()
        raise SearchUnavailable(f"HTTP {r.status_code}")
    found: list[Result] = []
    seen: set[str] = set()
    for hit in r.json().get("results") or []:
        url = hit.get("url") or ""
        # The same page reached through two engines comes back twice.
        if not url or url in seen:
            continue
        seen.add(url)
        found.append(
            Result(
                title=(hit.get("title") or "").strip(),
                url=url,
                snippet=(hit.get("content") or "").strip(),
            )
        )
        if len(found) == limit:
            break
    return found
