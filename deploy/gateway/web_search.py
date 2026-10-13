"""Keep Claude Code's web search from coming back empty without a word.

Claude Code's WebSearch is a request of its own: one user message and one
tool, the server tool `web_search_20250305`. The search runs
at whichever upstream serves the request. DeepSeek's API runs it and answers
with a `server_tool_use` block and the results; a vLLM server such as RUC's
does not, and answers with the model's text alone. Claude Code shows the agent
that text as a search that found nothing, with no error, so the agent goes on
without search and nobody hears that it is gone (about 27% of deepseek-flash
searches on 2026-10-11/12, after RUC began serving that group first).

Two hooks, so it holds whichever model a session runs:

- **Routing.** A request carrying a server web search tool is not sent to a
  deployment whose `model_info` says `supports_web_search: false`, the field
  LiteLLM's own filter reads (`router_utils.common_utils`), which only knows
  OpenAI's tool types. If the router's list was already narrowed to such a
  deployment (session affinity pins a conversation to one RUC key), the group's
  other deployments are offered instead, cooldowns aside.
- **The answer.** A streamed answer to a request whose only tool is web search is held
  until a `server_tool_use` block starts, and then passed on as it came. If the
  stream ends without one, the client gets a single Anthropic `error` event in
  its place, before any of the answer, so Claude Code reports it as an API
  error with this text rather than as "the search was cut off". An upstream's
  own error passes through untouched. Each such answer is logged as a warning
  naming the model group and the deployment, which is how a broken route shows.

Installed into site-packages as `cheese_web_search` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

import json
import re

from fastapi import HTTPException
from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_logger import CustomLogger

_SERVER_TOOL_USE = re.compile(rb'"type"\s*:\s*"server_tool_use"')
_ERROR_EVENT = re.compile(rb'(^|\n)event:\s*error\b|"type"\s*:\s*"error"')


def not_run(model: object) -> str:
    return (
        f"Web search did not run: the model '{model}' on this platform answered "
        "without searching the web. Do not retry WebSearch with this model; read "
        "pages whose address you know with WebFetch or cheese_fetch."
    )


def server_web_search(tools: object) -> str | None:
    """The name of an Anthropic server web search tool among ``tools``, if any."""
    for tool in tools if isinstance(tools, list) else ():
        if isinstance(tool, dict) and str(tool.get("type") or "").startswith("web_search_"):
            return str(tool.get("name") or "web_search")
    return None


def forces_web_search(data: dict) -> bool:
    """Whether a request exists only to search: its one tool is the server web
    search tool, as in Claude Code's WebSearch. Its `tool_choice` cannot say so:
    Claude Code 2.1.282's request reaches the gateway with `{"type": "auto"}`.
    A request that offers web search among other tools may answer without
    searching, so it is not held to this."""
    tools = data.get("tools")
    return (
        isinstance(tools, list)
        and len(tools) == 1
        and server_web_search(tools) is not None
    )


def executes_web_search(deployment: dict) -> bool:
    return (deployment.get("model_info") or {}).get("supports_web_search") is not False


def _group(model: str) -> list[dict]:
    from litellm.proxy.proxy_server import llm_router

    return (llm_router.get_model_list(model_name=model) or []) if llm_router else []


def _deployment_of(response: object) -> str:
    """The deployment that answered: its upstream model and LiteLLM's id for it,
    which `/model/info` lists against the row in config.yaml."""
    hidden = getattr(response, "_hidden_params", None) or {}
    return f"{hidden.get('litellm_model_name', 'unknown')} id={hidden.get('model_id', 'unknown')}"


def _as_bytes(chunk: object) -> bytes:
    if isinstance(chunk, bytes | bytearray):
        return bytes(chunk)
    if isinstance(chunk, str):
        return chunk.encode()
    if isinstance(chunk, dict):
        return json.dumps(chunk).encode()
    return b""


def error_event(message: str) -> bytes:
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return f"event: error\ndata: {json.dumps(body)}\n\n".encode()


def _record(data: dict, response: object) -> None:
    verbose_proxy_logger.warning(
        "cheese web_search not run: model_group=%s deployment=%s",
        data.get("model"),
        _deployment_of(response),
    )


class WebSearch(CustomLogger):
    async def async_filter_deployments(
        self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None
    ):
        if not server_web_search((request_kwargs or {}).get("tools")):
            return healthy_deployments
        able = [d for d in healthy_deployments if executes_web_search(d)]
        if able:
            return able
        # Affinity may have narrowed the list to a deployment that cannot
        # search; any of the group's that can is better than a silent answer.
        # These skip the router's cooldown list, so a cooling one may be tried.
        able = [d for d in _group(model) if executes_web_search(d)]
        return able or healthy_deployments

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data):
        if not forces_web_search(request_data):
            async for chunk in response:
                yield chunk
            return
        held: list | None = []
        seen = b""
        async for chunk in response:
            if held is None:
                yield chunk
                continue
            held.append(chunk)
            seen += _as_bytes(chunk)
            if _SERVER_TOOL_USE.search(seen) or _ERROR_EVENT.search(seen):
                for kept in held:
                    yield kept
                held = None
        if held is not None:
            _record(request_data, response)
            yield error_event(not_run(request_data.get("model")))

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        if not forces_web_search(data):
            return response
        content = response.get("content") if isinstance(response, dict) else getattr(response, "content", None)
        if isinstance(content, list) and not any(
            (block.get("type") if isinstance(block, dict) else getattr(block, "type", None)) == "server_tool_use"
            for block in content
        ):
            _record(data, response)
            raise HTTPException(status_code=400, detail=not_run(data.get("model")))
        return response


handler = WebSearch()
