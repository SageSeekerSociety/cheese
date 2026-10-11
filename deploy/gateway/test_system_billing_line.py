"""A request to an upstream that is not Anthropic arrives without Claude Code's
billing line and otherwise as sent; a request bound for Anthropic arrives as sent.

Runs the hook and then the real router call against a local upstream, so a
LiteLLM upgrade that rebuilds `system` after the hook fails here.
"""

import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import litellm.proxy.proxy_server as proxy_server
from litellm.router import Router

from cheese_billing_line import handler

BILLING = "x-anthropic-billing-header: cc_version=2.1.282.772; cc_entrypoint=sdk-cli; cch=64903;"
SYSTEM = [
    {"type": "text", "text": BILLING},
    {"type": "text", "text": "You are Claude Code."},
    {"type": "text", "text": "Project rules.", "cache_control": {"type": "ephemeral"}},
]
SEEN: list[dict] = []

REPLY = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
    "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        SEEN.append(json.loads(self.rfile.read(int(self.headers.get("content-length") or 0))))
        body = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def request(model):
    return {
        "model": model,
        "max_tokens": 8,
        "system": copy.deepcopy(SYSTEM),
        "messages": [{"role": "user", "content": "hi"}],
    }


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    router = Router(
        model_list=[
            {"model_name": "open-model", "litellm_params": {"model": "anthropic/m", "api_base": base, "api_key": "k"}},
            {"model_name": "claude", "litellm_params": {"model": "anthropic/claude-x", "api_key": "k"}},
            {"model_name": "claude-pinned", "litellm_params": {"model": "anthropic/claude-x", "api_base": "https://api.anthropic.com", "api_key": "k"}},
            {"model_name": "mixed", "litellm_params": {"model": "anthropic/m", "api_base": base, "api_key": "k"}},
            {"model_name": "mixed", "litellm_params": {"model": "anthropic/claude-x", "api_key": "k"}},
        ]
    )
    with patch.object(proxy_server, "llm_router", router):
        data = await handler.async_pre_call_hook(None, None, request("open-model"), "anthropic_messages")
        await router.aanthropic_messages(**data)
        for model in ("claude", "claude-pinned", "mixed"):
            kept = await handler.async_pre_call_hook(None, None, request(model), "anthropic_messages")
            assert kept["system"] == SYSTEM, (model, kept["system"])
    server.shutdown()

    assert len(SEEN) == 1, SEEN
    texts = [b.get("text") for b in SEEN[0]["system"]]
    assert texts == ["You are Claude Code.", "Project rules."], texts
    assert SEEN[0]["system"][1].get("cache_control") == {"type": "ephemeral"}, SEEN[0]["system"]
    print("PASS: billing line left out for a non-Anthropic upstream, kept for Anthropic")


asyncio.run(main())
