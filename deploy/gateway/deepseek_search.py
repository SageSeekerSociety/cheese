"""DeepSeek's search API as a LiteLLM search provider.

The platform's own web search (`cheese_web(action=search)`, backend `/web-search`) is
asked through this gateway with the caller's virtual key, so a search is
admitted by that key's budget, logged on that key, and drained into credits
with its model calls (`backend/app/domain/agent/gateway.py`). LiteLLM 1.103.3
has the search API (`/v1/search/{search_tool_name}`) but no DeepSeek provider;
this is one, registered by `patch_deepseek_search_provider.py` and named in
`config.yaml`'s `search_tools`.

DeepSeek's `POST /v1/tools/search` (https://api-docs.deepseek.com/zh-cn/guides/search/)
takes a query of at most 128 characters and 1-50 results, and is billed per
successful request: a failed, empty or timed-out search costs nothing. So an
answer with no results carries a cost of zero here, and LiteLLM's per-query
price is charged only for one that found something.

Installed into site-packages as `cheese_deepseek_search`.
"""

from __future__ import annotations

import httpx
import litellm
from litellm.llms.base_llm.search.transformation import (
    BaseSearchConfig,
    SearchResponse,
    SearchResult,
)

ENDPOINT = "https://api.deepseek.com/v1/tools/search"
MAX_QUERY_CHARS = 128
MAX_RESULTS = 50

#: USD per successful search. Our DeepSeek account is billed in CNY, so the
#: price is the Chinese list's ¥5 per 1,000 successful requests
#: (https://api-docs.deepseek.com/zh-cn/guides/search/, read 2026-10-21) at
#: the gateway's RMB 7.1/USD (config.yaml's pricing note): 5 / 1000 / 7.1.
#: config.yaml prices the searches DeepSeek runs inside a model call
#: (`search_context_cost_per_query`) at the same figure; test_deepseek_search.py
#: holds the two together.
USD_PER_SEARCH = 0.000704

litellm.register_model(
    {
        "deepseek/search": {
            "litellm_provider": "deepseek",
            "mode": "search",
            "input_cost_per_query": USD_PER_SEARCH,
        }
    }
)


class DeepSeekSearchConfig(BaseSearchConfig):
    @staticmethod
    def ui_friendly_name() -> str:
        return "DeepSeek"

    def get_http_method(self):
        return "POST"

    def validate_environment(
        self,
        headers: dict,
        api_key: str | None = None,
        api_base: str | None = None,
        **kwargs,
    ) -> dict:
        key = self.resolve_server_api_key(
            caller_api_key=api_key,
            caller_api_base=api_base,
            key_env_vars=("DEEPSEEK_API_KEY",),
            base_env_var="DEEPSEEK_SEARCH_API_BASE",
            default_api_base=ENDPOINT,
        )
        if not key:
            raise ValueError("DEEPSEEK_API_KEY is not set for the DeepSeek search tool")
        headers["Authorization"] = f"Bearer {key}"
        headers["Content-Type"] = "application/json"
        return headers

    def get_complete_url(
        self,
        api_base: str | None,
        optional_params: dict,
        data: dict | list[dict] | None = None,
        **kwargs,
    ) -> str:
        return api_base or ENDPOINT

    def transform_search_request(
        self,
        query: str | list[str],
        optional_params: dict,
        **kwargs,
    ) -> dict:
        if isinstance(query, list):
            query = " ".join(query)
        count = int(optional_params.get("max_results") or 10)
        return {
            "query": query[:MAX_QUERY_CHARS],
            "count": max(1, min(count, MAX_RESULTS)),
        }

    def transform_search_response(
        self,
        raw_response: httpx.Response,
        logging_obj,
        **kwargs,
    ) -> SearchResponse:
        results = [
            SearchResult(
                title=(hit.get("title") or "").strip(),
                url=hit.get("url") or "",
                # The longer summary DeepSeek writes for a model, else its snippet.
                snippet=(hit.get("summary") or hit.get("snippet") or "").strip(),
                date=hit.get("date"),
                last_updated=None,
            )
            for hit in raw_response.json().get("results") or []
            if hit.get("url")
        ]
        response = SearchResponse(results=results, object="search")
        if not results:
            # DeepSeek bills nothing for an empty search; LiteLLM's per-query
            # price would charge one. A response cost in _hidden_params is the
            # cost LiteLLM logs (litellm_logging._response_cost_calculator).
            response._hidden_params = {"response_cost": 0.0}
        return response
