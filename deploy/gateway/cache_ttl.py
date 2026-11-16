"""Ask an upstream that reads the cache lifetime only at the top of a request
for the lifetime its row declares.

Moonshot's Messages endpoint (kimi-k3, kimi-k3.1-pro) caches every prompt
prefix and takes the cache lifetime from one place: "Anthropic Messages API
使用顶层 `cache_control` 设置缓存 TTL。省略该字段时，默认按 `5m` TTL 读写缓存"
and "`cache_control` 只在请求顶层生效，消息体内的同名标记会被忽略"
(https://platform.kimi.com/docs/guide/context-caching, read 2026-11-17).
Claude Code writes its marks only inside the messages, so without this every
Kimi conversation idle for five minutes, which is every turn spent waiting on
a person, starts again from an empty cache. A deployment opts in under
`model_info`:

    cheese_cache_ttl: "1h"

Like `no_explicit_cache.py` this runs after the router has picked the
deployment, so another row of the same group (a relay with its own caching)
is sent the request as before. A request that already carries a top-level
`cache_control` keeps its own: the caller chose.

Installed into site-packages as `cheese_cache_ttl` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

from litellm.integrations.custom_logger import CustomLogger

KEY = "cheese_cache_ttl"
TTLS = frozenset({"5m", "1h"})


class CacheTtl(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        info = kwargs.get("model_info")
        ttl = info.get(KEY) if isinstance(info, dict) else None
        if ttl not in TTLS or kwargs.get("cache_control") is not None:
            return None
        kwargs["cache_control"] = {"type": "ephemeral", "ttl": ttl}
        return kwargs


handler = CacheTtl()
