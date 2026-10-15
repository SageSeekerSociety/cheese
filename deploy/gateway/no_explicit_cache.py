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


def without_cache_control(value):
    if isinstance(value, dict):
        return {k: without_cache_control(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [without_cache_control(v) for v in value]
    return value


class NoExplicitCache(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        info = kwargs.get("model_info")
        if not isinstance(info, dict) or info.get(FLAG) is not True:
            return None
        for key in ("messages", "system", "tools"):
            if key in kwargs:
                kwargs[key] = without_cache_control(kwargs[key])
        return kwargs


handler = NoExplicitCache()
