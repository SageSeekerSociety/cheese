"""A request routed to ChatGPT reaches it with the caller's session as `session_id`.

Runs the hook and then the real router call against a local upstream, so a
LiteLLM upgrade that stops passing `extra_headers` down the Messages-to-Responses
path fails here rather than silently dropping the cache again.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import litellm.proxy.proxy_server as proxy_server
from litellm.router import Router

from cheese_chatgpt_session import handler

SESSION = "0f1e2d3c-1111-2222-3333-444455556666"
SEEN: list[tuple[str, dict]] = []

EVENTS = [
    {"type": "response.created", "response": {"id": "r", "object": "response", "status": "in_progress", "model": "m", "output": []}},
    {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "i", "role": "assistant", "content": [], "status": "in_progress"}},
    {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "item_id": "i", "delta": "ok"},
    {"type": "response.completed", "response": {"id": "r", "object": "response", "status": "completed", "model": "m", "output": [{"type": "message", "id": "i", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "ok", "annotations": []}]}], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
]


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        SEEN.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in EVENTS:
            self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())


async def call(router, model, headers):
    data = {
        "model": model,
        "max_tokens": 8,
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
        "proxy_server_request": {"headers": headers},
    }
    data = await handler.async_pre_call_hook(None, None, data, "anthropic_messages")
    data.pop("proxy_server_request")
    response = await router.aanthropic_messages(**data)
    async for _ in response:
        pass


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    router = Router(
        model_list=[
            {"model_name": "via-chatgpt", "litellm_params": {"model": "openai/m", "api_base": f"{base}/chatgpt/acct", "api_key": "k"}},
            {"model_name": "elsewhere", "litellm_params": {"model": "openai/m", "api_base": f"{base}/elsewhere", "api_key": "k"}},
        ]
    )
    with patch.object(proxy_server, "llm_router", router):
        await call(router, "via-chatgpt", {"X-Claude-Code-Session-Id": SESSION})
        await call(router, "elsewhere", {"X-Claude-Code-Session-Id": SESSION})
        await call(router, "via-chatgpt", {})
        await call(router, "via-chatgpt", {"X-Claude-Code-Session-Id": "bad value\r\nx: y"})
    server.shutdown()

    sessions = [(path.split("/")[1], headers.get("session_id")) for path, headers in SEEN]
    assert sessions == [
        ("chatgpt", SESSION),
        ("elsewhere", None),
        ("chatgpt", None),
        ("chatgpt", None),
    ], sessions


asyncio.run(main())
print("chatgpt session ok")
