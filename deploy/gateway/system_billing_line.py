"""Leave Claude Code's billing line out of requests to upstreams that are not Anthropic.

Claude Code opens every request's system prompt with a block
`x-anthropic-billing-header: cc_version=...; ...; cch=<hash>; cc_prompt_id=...;`
when it believes it is talking to Anthropic, which a sandbox does: the metering
proxy answers for api.anthropic.com. `cch` is a hash of the request body, so it
differs on every request, and `cc_prompt_id` on every new prompt. An upstream
that caches by prefix — vLLM behind RUC's endpoint, ChatGPT's Codex backend —
therefore misses from the first line on and serves the whole conversation
uncached. Measured on RUC on 2026-10-11: 0.3% of input from cache with the
line, 162,752 of 163,195 tokens on the next turn without it. DeepSeek's own API
already ignores the line, which is why it hit 98% throughout.

The line means something only to Anthropic, so this pre-call hook removes it
for model groups whose deployments all name a non-Anthropic `api_base`, and
leaves every other request as it came.

Installed into site-packages as `cheese_billing_line` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

from urllib.parse import urlsplit

from litellm.integrations.custom_logger import CustomLogger

BILLING_LINE = "x-anthropic-billing-header:"


def routes_off_anthropic(model: object) -> bool:
    """Whether every deployment of a model group names an upstream other than
    Anthropic's API. A deployment without an `api_base` goes to Anthropic."""
    from litellm.proxy.proxy_server import llm_router

    if llm_router is None or not isinstance(model, str) or not model:
        return False
    deployments = llm_router.get_model_list(model_name=model) or []
    if not deployments:
        return False
    for deployment in deployments:
        base = str((deployment.get("litellm_params") or {}).get("api_base") or "")
        if not base or urlsplit(base).hostname in (None, "api.anthropic.com"):
            return False
    return True


def without_billing_line(system: object) -> object:
    """``system`` with the billing block removed; anything else unchanged."""
    if not isinstance(system, list):
        return system
    return [
        block
        for block in system
        if not (
            isinstance(block, dict)
            and isinstance(block.get("text"), str)
            and block["text"].startswith(BILLING_LINE)
        )
    ]


class BillingLine(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if "system" in data and routes_off_anthropic(data.get("model")):
            data["system"] = without_billing_line(data["system"])
        return data


handler = BillingLine()
