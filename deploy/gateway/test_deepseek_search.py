"""DeepSeek's search API as the gateway's search tool (`deepseek_search.py`).

Run in the image build after `patch_deepseek_search_provider.py`. Holds what
the backend's web search relies on: the provider is registered under the
name `config.yaml`'s search tool gives, it sends DeepSeek the query and the
count DeepSeek accepts, a search that found something is priced at the
search price `config.yaml` gives the searches DeepSeek runs inside a model
call, and one that found nothing costs nothing, as DeepSeek bills it.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import litellm
import yaml
from litellm.integrations.custom_logger import CustomLogger

import cheese_deepseek_search as provider

SENT: list[dict] = []


async def deepseek(self, request: httpx.Request, **kwargs) -> httpx.Response:
    body = json.loads(request.content)
    SENT.append({
        "url": str(request.url),
        "auth": request.headers.get("authorization"),
        "body": body,
        "timeout": request.extensions.get("timeout") or {},
    })
    hits = [] if body["query"] == "nothing" else [
        {"title": " A page ", "url": "https://example.com/a", "summary": "for a model",
         "snippet": "short", "date": "2026-10-01T00:00:00Z"},
        {"title": "no address", "url": ""},
    ]
    return httpx.Response(200, request=request, json={"results": hits})


class Costs(CustomLogger):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[float] = []

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self.seen.append(kwargs.get("response_cost"))


async def search(costs: Costs, query: str):
    before = len(costs.seen)
    found = await litellm.asearch(
        search_provider="deepseek", query=query, max_results=80, api_key="k"
    )
    for _ in range(100):
        if len(costs.seen) > before:
            break
        await asyncio.sleep(0.02)
    return found, costs.seen[-1]


async def main() -> None:
    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    (tool,) = [t for t in config["search_tools"] if t["search_tool_name"] == "deepseek-search"]
    assert tool["litellm_params"]["search_provider"] == "deepseek", tool
    assert tool["litellm_params"]["api_key"] == "os.environ/DEEPSEEK_API_KEY", tool
    official = next(
        row for row in config["model_list"]
        if row["model_name"] == "deepseek-flash"
        and row["litellm_params"].get("api_key") == "os.environ/DEEPSEEK_API_KEY"
    )
    in_call = official["model_info"]["search_context_cost_per_query"]
    assert set(in_call.values()) == {provider.USD_PER_SEARCH}, (in_call, provider.USD_PER_SEARCH)
    assert abs(provider.USD_PER_SEARCH - 5 / 1000 / 7.1) < 5e-7, provider.USD_PER_SEARCH

    costs = Costs()
    litellm.callbacks = [costs]
    with patch.object(httpx.AsyncClient, "send", deepseek):
        found, cost = await search(costs, "hello")
        assert SENT[-1]["url"] == provider.ENDPOINT, SENT[-1]
        assert SENT[-1]["auth"] == "Bearer k", SENT[-1]
        assert SENT[-1]["body"] == {"query": "hello", "count": 50}, SENT[-1]
        assert [(r.title, r.url, r.snippet, r.date) for r in found.results] == [
            ("A page", "https://example.com/a", "for a model", "2026-10-01T00:00:00Z")
        ], found
        assert cost == provider.USD_PER_SEARCH, cost

        long = "长" * 200
        empty, cost = await search(costs, "nothing")
        assert empty.results == [] and cost == 0.0, (empty, cost)
        await search(costs, long)
        assert SENT[-1]["body"]["query"] == long[:128], SENT[-1]

        # As the proxy calls it: the search tool from config.yaml, through the
        # router, which hands the tool's own params (its timeout) to the call.
        router = litellm.Router(
            model_list=[],
            search_tools=[{**tool, "litellm_params": {**tool["litellm_params"], "api_key": "k"}}],
        )
        await router.asearch(model="deepseek-search", query="hello")
        assert SENT[-1]["timeout"]["read"] <= 15, SENT[-1]
    print(
        "PASS: DeepSeek search is the gateway's search tool; a search that found "
        f"something costs ${provider.USD_PER_SEARCH}, the price config.yaml gives "
        "searches inside a model call, and one that found nothing costs nothing"
    )


asyncio.run(main())
