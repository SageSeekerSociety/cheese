"""Send a deployment the headers its upstream asks for, and only that deployment.

Some upstreams want headers of their own on every request. OpenCode Go
(opencode.ai/inference/go) asks for a User-Agent naming the client, not a
generic SDK, and a stable session per conversation (`x-opencode-session`),
which it routes and prompt-caches by; it refuses a request without the session
(400 MissingSessionID, measured 2026-10-14). A deployment declares them under
its `model_info`, in config.yaml or on a row added at runtime:

    cheese_upstream_headers: {user-agent: cheese-gateway/1.0}
    cheese_session_header: x-opencode-session

`cheese_upstream_headers` is sent as written. `cheese_session_header` names
the header that carries Claude Code's session (`X-Claude-Code-Session-Id`),
validated as `cheese_chatgpt_session.caller_session` validates it. A row that
declares the header always sends it, because Go refuses a request without one.
A caller with no valid session of its own (pi on /v1/chat/completions, the
backend's `GatewayChat` features) gets one derived from the request: a hash of
the caller's virtual key and its first user message, which every request of
one conversation repeats, so the conversation keeps one id and its cache, and
two callers never share one. Only a request with no user message gets a fresh
random id. Never text the caller wrote, so nothing but a hash reaches the
upstream.

This runs after the router has picked the deployment, on the native Messages
path and again inside the Messages-to-chat translation, so another row of the
same model group, such as the pay-per-use API it falls back to, is sent none
of it.

Installed into site-packages as `cheese_upstream_headers` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

import hashlib
import json
import uuid

from litellm.integrations.custom_logger import CustomLogger

from cheese_chatgpt_session import caller_session

STATIC = "cheese_upstream_headers"
SESSION = "cheese_session_header"


def declared_headers(model_info: object) -> dict[str, str]:
    """The fixed headers a deployment declares; anything but text is ignored."""
    declared = (model_info or {}).get(STATIC) if isinstance(model_info, dict) else None
    if not isinstance(declared, dict):
        return {}
    return {k: v for k, v in declared.items() if isinstance(k, str) and isinstance(v, str)}


def _without_cache_marks(value):
    # Claude Code moves its cache_control breakpoints from turn to turn; the
    # content they sit on is what stays the same.
    if isinstance(value, dict):
        return {k: _without_cache_marks(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_without_cache_marks(v) for v in value]
    return value


def derived_session(kwargs: dict) -> str:
    """A session for a caller that sent none: the same for every request of one
    conversation from one key, different across keys and conversations."""
    metadata = kwargs.get("litellm_metadata") or kwargs.get("metadata") or {}
    key = metadata.get("user_api_key_hash") or metadata.get("user_api_key") or ""
    first = next(
        (m.get("content") for m in kwargs.get("messages") or [] if isinstance(m, dict) and m.get("role") == "user"),
        None,
    )
    if first is None:
        return str(uuid.uuid4())
    if isinstance(first, list) and len(first) == 1 and isinstance(first[0], dict) and first[0].get("type") == "text":
        # The Messages-to-chat translation may turn one text block into a plain
        # string; both spell the same message.
        first = first[0].get("text")
    seed = json.dumps([str(key), _without_cache_marks(first)], sort_keys=True, ensure_ascii=False)
    return "cheese-" + hashlib.sha256(seed.encode()).hexdigest()[:32]


class UpstreamHeaders(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        info = kwargs.get("model_info")
        headers = declared_headers(info)
        session = info.get(SESSION) if isinstance(info, dict) else None
        if isinstance(session, str) and session:
            headers[session] = caller_session(kwargs) or derived_session(kwargs)
        if not headers:
            return None
        # A new mapping: the one in kwargs may be the deployment's own
        # litellm_params.extra_headers, shared by every request it serves.
        kwargs["extra_headers"] = {**(kwargs.get("extra_headers") or {}), **headers}
        return kwargs


handler = UpstreamHeaders()
