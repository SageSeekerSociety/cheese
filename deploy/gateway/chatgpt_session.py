"""Name the Claude Code session on requests that go to ChatGPT's Codex backend.

The Codex backend sends requests that carry the same `session_id` header to the
same machine, and only there does a conversation's prompt cache hit. Without
the header, requests land on random machines and hit only the tool definitions
every machine has cached (#2597: 9% instead of nearly all of the input).

Claude Code names its session in `X-Claude-Code-Session-Id`. LiteLLM does not
forward client headers on the Messages-to-Responses path, so this pre-call hook
copies the value into `extra_headers` as `session_id`, for models routed to the
metering proxy's ChatGPT listener (`/chatgpt/<account>`) only. Other upstreams
are not sent the caller's session.

Installed into site-packages as `cheese_chatgpt_session` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

import re

from litellm.integrations.custom_logger import CustomLogger

SESSION_HEADER = "x-claude-code-session-id"
# Claude Code's session ids are UUIDs. Anything else is not forwarded, so a
# caller cannot put arbitrary text into a header sent upstream.
_SESSION_ID = re.compile(r"[A-Za-z0-9-]{8,128}")


def routes_to_chatgpt(model: object) -> bool:
    """Whether a model group's deployments go to the ChatGPT listener."""
    from litellm.proxy.proxy_server import llm_router

    if llm_router is None or not isinstance(model, str) or not model:
        return False
    return any(
        "/chatgpt/" in str((d.get("litellm_params") or {}).get("api_base") or "")
        for d in llm_router.get_model_list(model_name=model) or []
    )


def caller_session(data: dict) -> str | None:
    headers = (data.get("proxy_server_request") or {}).get("headers") or {}
    for key, value in headers.items():
        if key.lower() == SESSION_HEADER and isinstance(value, str):
            return value if _SESSION_ID.fullmatch(value) else None
    return None


class ChatGPTSession(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        session = caller_session(data)
        if session and routes_to_chatgpt(data.get("model")):
            data["extra_headers"] = {
                **(data.get("extra_headers") or {}),
                "session_id": session,
            }
        return data


handler = ChatGPTSession()
