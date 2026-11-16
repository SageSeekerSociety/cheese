"""A Kimi row that declares cheese_cache_ttl asks Moonshot for that cache
lifetime at the top of every request, which is the only place Moonshot reads
it; a row without the key sends the request as it came, and a caller's own
top-level choice is kept.

Rows on the native Messages path against a local upstream, called through the
router the way the proxy calls a Messages request (buffered and streamed) and
a chat request, with Claude Code's cache marks inside the messages. What is
checked is the body the upstream received.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litellm
from litellm.router import Router

from cheese_cache_ttl import handler

BODIES: list[dict] = []
MESSAGE = {
    "id": "m",
    "type": "message",
    "role": "assistant",
    "model": "kimi-k3",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
STREAM = [
    ("message_start", {"type": "message_start", "message": {**MESSAGE, "content": [], "stop_reason": None}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
    ("message_stop", {"type": "message_stop"}),
]


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
        BODIES.append(body)
        if body.get("stream"):
            data = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in STREAM).encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
        else:
            data = json.dumps(MESSAGE).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


MARKED = [
    {
        "role": "user",
        "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
    }
]


async def messages(router, model, stream=False, **extra):
    BODIES.clear()
    response = await router.aanthropic_messages(
        model=model, max_tokens=8, messages=MARKED, stream=stream, **extra
    )
    if stream:
        async for _ in response:
            pass
    assert len(BODIES) == 1, BODIES
    return BODIES[0]


async def chat(router, model):
    BODIES.clear()
    await router.acompletion(model=model, max_tokens=8, messages=[{"role": "user", "content": "hi"}])
    assert len(BODIES) == 1, BODIES
    return BODIES[0]


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    litellm.callbacks = [handler]

    def row(name, info):
        return {
            "model_name": name,
            "litellm_params": {"model": "anthropic/kimi-k3", "api_key": "k", "api_base": base},
            "model_info": info,
        }

    router = Router(
        model_list=[
            row("hour", {"cheese_cache_ttl": "1h"}),
            row("default", {}),
            row("garbled", {"cheese_cache_ttl": "60"}),
        ]
    )
    hour = {"type": "ephemeral", "ttl": "1h"}

    sent = await messages(router, "hour")
    assert sent.get("cache_control") == hour, sent
    # Claude Code's own marks still reach the upstream inside the messages.
    assert sent["messages"][0]["content"][0].get("cache_control") == {"type": "ephemeral"}, sent
    sent = await messages(router, "hour", stream=True)
    assert sent.get("cache_control") == hour, sent
    sent = await chat(router, "hour")
    assert sent.get("cache_control") == hour, sent

    for model in ("default", "garbled"):
        sent = await messages(router, model)
        assert "cache_control" not in sent, sent

    caller = {"type": "ephemeral", "ttl": "5m"}
    sent = await messages(router, "hour", cache_control=caller)
    assert sent.get("cache_control") == caller, sent

    server.shutdown()
    print("cache ttl ok")


asyncio.run(main())
