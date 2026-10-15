"""Strip cache_control for a deployment whose upstream refuses explicit caching.

Claude Code marks cache breakpoints (`cache_control`) on every request. For a
Gemini model LiteLLM turns those marks into a `cachedContents` object, and a
model without explicit caching refuses the whole request: gemini-4-argon
answers 400 "Explicit caching is not supported for models/gemini-4-argon"
(measured 2026-10-15) on any prompt above Gemini's 1,024-token caching floor,
which is every Claude Code request. Gemini caches prompt prefixes implicitly,
so dropping the marks costs nothing. A deployment opts in under `model_info`:

    cheese_no_explicit_cache: true

Like `cheese_upstream_headers` this runs after the router has picked the
deployment, inside the Messages-to-chat translation, so another row of the
same group is sent the marks as before.

Installed into site-packages as `cheese_no_explicit_cache` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

from litellm.integrations.custom_logger import CustomLogger

FLAG = "cheese_no_explicit_cache"


def _unmarked(item):
    if isinstance(item, dict) and "cache_control" in item:
        return {k: v for k, v in item.items() if k != "cache_control"}
    return item


def _block(block):
    """A content block, and the blocks a tool result nests, without marks.
    Nothing deeper: a tool's arguments and schemas are the caller's data, and
    a property may well be called cache_control."""
    block = _unmarked(block)
    if isinstance(block, dict) and isinstance(block.get("content"), list):
        block = {**block, "content": [_unmarked(b) for b in block["content"]]}
    return block


def _message(message):
    message = _unmarked(message)
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        message = {**message, "content": [_block(b) for b in message["content"]]}
    return message


class NoExplicitCache(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        info = kwargs.get("model_info")
        if not isinstance(info, dict) or info.get(FLAG) is not True:
            return None
        # Where the Messages and chat formats put the marks: on a message, a
        # content block, a system block, a tool.
        if isinstance(kwargs.get("messages"), list):
            kwargs["messages"] = [_message(m) for m in kwargs["messages"]]
        if isinstance(kwargs.get("system"), list):
            kwargs["system"] = [_unmarked(b) for b in kwargs["system"]]
        if isinstance(kwargs.get("tools"), list):
            kwargs["tools"] = [_unmarked(t) for t in kwargs["tools"]]
        return kwargs


handler = NoExplicitCache()
