"""A deployment marked cheese_no_explicit_cache never asks Gemini for a
cachedContents object, however Claude Code marks its prompt; an unmarked one
still does.

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
        self.rfile.read(int(self.headers.get("content-length") or 0))
        SEEN.append(self.path)
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


async def call(router, model):
    SEEN.clear()
    long_text = "The quick brown fox jumps over the lazy dog. " * 400
    await router.aanthropic_messages(
        model=model,
        max_tokens=8,
        system=[{"type": "text", "text": long_text, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
            }
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
    unmarked = await call(router, "unmarked")
    assert any("cachedContents" in p for p in unmarked), unmarked
    server.shutdown()
    print("no explicit cache ok")


asyncio.run(main())
