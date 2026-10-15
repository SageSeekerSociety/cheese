"""A deployment marked cheese_no_explicit_cache never asks Gemini for a
cachedContents object, however Claude Code marks its prompt, and a tool's own
data named cache_control reaches Gemini untouched; an unmarked one still asks.

Two rows of the same Gemini model against a local upstream, called through the
router the way the proxy calls a Messages request, with a prompt above
Gemini's 1,024-token caching floor and Claude Code's cache_control marks on
the system prompt and the last user block.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litellm
from litellm.router import Router

from cheese_no_explicit_cache import handler

SEEN: list[str] = []


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        SEEN.append(self.path)
        if "generateContent" in self.path:
            BODIES.append(json.loads(body))
        if "cachedContents" in self.path:
            return self.reply({"name": "cachedContents/abc", "model": "models/gemini-4-argon"})
        return self.reply(
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "ok"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 2000, "candidatesTokenCount": 1, "totalTokenCount": 2001},
            }
        )

    def do_GET(self):
        SEEN.append(self.path)
        self.reply({"cachedContents": []})

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


BODIES: list[dict] = []

# A tool whose own data says cache_control: the strip must leave it alone.
TOOL = {
    "name": "set_header",
    "description": "Set a header.",
    "input_schema": {
        "type": "object",
        "properties": {"cache_control": {"type": "string"}},
        "required": ["cache_control"],
    },
    "cache_control": {"type": "ephemeral"},
}


async def call(router, model):
    SEEN.clear()
    BODIES.clear()
    long_text = "The quick brown fox jumps over the lazy dog. " * 400
    await router.aanthropic_messages(
        model=model,
        max_tokens=8,
        system=[{"type": "text", "text": long_text, "cache_control": {"type": "ephemeral"}}],
        tools=[TOOL],
        messages=[
            {"role": "user", "content": "set it"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "set_header", "input": {"cache_control": "no-store"}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "done", "cache_control": {"type": "ephemeral"}}],
                    },
                    {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
                ],
            },
        ],
    )
    return list(SEEN)


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    litellm.callbacks = [handler]

    def row(name, info):
        return {
            "model_name": name,
            "litellm_params": {"model": "gemini/gemini-4-argon", "api_key": "k", "api_base": base},
            "model_info": info,
        }

    router = Router(
        model_list=[
            row("marked", {"cheese_no_explicit_cache": True}),
            row("unmarked", {}),
        ]
    )
    marked = await call(router, "marked")
    assert marked and not any("cachedContents" in p for p in marked), marked
    sent = json.dumps(BODIES[-1])
    # The tool's schema and its earlier call keep their own cache_control.
    assert sent.count("cache_control") >= 3, sent
    assert "no-store" in sent, sent
    unmarked = await call(router, "unmarked")
    assert any("cachedContents" in p for p in unmarked), unmarked
    server.shutdown()
    print("no explicit cache ok")


asyncio.run(main())
