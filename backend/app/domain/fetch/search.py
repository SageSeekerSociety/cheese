"""Search the web for an agent, through DeepSeek's search API.

Codex and pi have no search tool at all, and Claude Code's WebSearch is a tool
the model provider executes, so on a model whose upstream does not run it the
agent gets no results. Agents in those sessions were opening search engines'
result pages with ``cheese_fetch`` instead — measured, 153 fetches of Bing,
Google, Baidu and Google Scholar result URLs in two weeks, the last of which
answer a crawler with a block page.

DeepSeek's ``POST /v1/tools/search`` answers a query with ranked results and a
summary of each written for a model to read
(https://api-docs.deepseek.com/zh-cn/guides/search/). It is billed per
successful request, not per result, so asking for fewer results saves nothing
but the caller's context. Off until the deployment has a DeepSeek key.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

ENDPOINT = "https://api.deepseek.com/v1/tools/search"

#: The API refuses a longer query.
MAX_QUERY_CHARS = 128

#: A search answers in a second or two; past this the caller is better told so
#: than kept waiting. Not retried here: the agent sees the status and decides.
SEARCH_TIMEOUT_SECONDS = 15.0


class SearchUnavailable(Exception):
    """The search service could not be asked, or did not answer."""


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    #: The longer ``summary`` the API writes for models, else its short snippet.
    snippet: str
    #: When the page was published, RFC 3339, when the API knows.
    date: str | None = None


async def search(query: str, api_key: str, *, limit: int = 8) -> list[Result]:
    """The first ``limit`` results for ``query``, best first."""
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            r = await client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query[:MAX_QUERY_CHARS], "count": limit},
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
        if not url or url in seen:
            continue
        seen.add(url)
        found.append(
            Result(
                title=(hit.get("title") or "").strip(),
                url=url,
                snippet=(hit.get("summary") or hit.get("snippet") or "").strip(),
                date=hit.get("date") or None,
            )
        )
        if len(found) == limit:
            break
    return found
