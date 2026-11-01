"""Search the web for an agent, through the gateway's DeepSeek search tool.

Codex and pi have no search tool at all, and Claude Code's WebSearch is a tool
the model provider executes, so on a model whose upstream does not run it the
agent gets no results. Agents in those sessions were opening search engines'
result pages with ``cheese_web(action=fetch)`` instead — measured, 153 fetches of Bing,
Google, Baidu and Google Scholar result URLs in two weeks, the last of which
answer a crawler with a block page.

The search is asked of the gateway (LiteLLM's ``/v1/search/{tool}``) on the
caller's own virtual key, the key its model calls are made on: the gateway
holds DeepSeek's key, refuses a key past its budget, and logs the search's
price on the key, from where it is drained into credits with the key's model
calls (``agent.gateway_spend``). DeepSeek's ``POST /v1/tools/search``
(https://api-docs.deepseek.com/zh-cn/guides/search/) answers a query with
ranked results and a summary of each written for a model to read, and bills a
successful request, not a result, so asking for fewer results saves nothing
but the caller's context. The provider is ``deploy/gateway/deepseek_search.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

#: The gateway's search tool (``search_tools`` in deploy/gateway/config.yaml).
SEARCH_TOOL = "deepseek-search"

#: DeepSeek refuses a longer query.
MAX_QUERY_CHARS = 128

#: A search answers in a second or two; past this the caller is better told so
#: than kept waiting. Not retried here: the agent sees the status and decides.
SEARCH_TIMEOUT_SECONDS = 15.0


class SearchUnavailable(Exception):
    """The search could not be asked, or was not answered."""


class SearchRefused(Exception):
    """The gateway refused the caller's key: its budget is spent."""


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    #: The longer summary DeepSeek writes for models, else its short snippet.
    snippet: str
    #: When the page was published, RFC 3339, when the search knows.
    date: str | None = None


async def search(query: str, *, gateway: str, key: str, limit: int = 8) -> list[Result]:
    """The first ``limit`` results for ``query``, best first, asked of the
    gateway at ``gateway`` on the virtual key ``key``."""
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            r = await client.post(
                f"{gateway.rstrip('/')}/v1/search/{SEARCH_TOOL}",
                headers={"Authorization": f"Bearer {key}"},
                json={"query": query[:MAX_QUERY_CHARS], "max_results": limit},
            )
    except httpx.HTTPError as exc:
        raise SearchUnavailable(type(exc).__name__) from exc
    if r.status_code == 429 and "budget" in r.text.lower():
        raise SearchRefused()
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
                snippet=(hit.get("snippet") or "").strip(),
                date=hit.get("date") or None,
            )
        )
        if len(found) == limit:
            break
    return found
