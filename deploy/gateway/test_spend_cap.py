"""A deployment with cheese_spend_cap_usd is refused once its upstream model
has cost that much, and the upstream is not called; under the cap, and for a
deployment with no cap, calls go through. Calls made since the ledger was
read count toward the cap before the next read.

A Gemini row against a local upstream, called through the router the way the
proxy calls a Messages request, with the ledger replaced by a figure the test
sets.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litellm
from litellm.router import Router

from cheese_spend_cap import SpendCap

CALLS: list[str] = []
LEDGER = {"gemini/gemini-4-argon": 0.0}
ASKED: list[str] = []


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        CALLS.append(self.path)
        body = json.dumps(
            {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                # 1M in at $4/M: every call costs $4.
                "usageMetadata": {"promptTokenCount": 1_000_000, "candidatesTokenCount": 0, "totalTokenCount": 1_000_000},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def spend_of(model):
    ASKED.append(model)
    return LEDGER.get(model, 0.0)


async def call(router, name):
    CALLS.clear()
    try:
        await router.aanthropic_messages(
            model=name, max_tokens=8, messages=[{"role": "user", "content": "hi"}]
        )
    except Exception as error:  # noqa: BLE001 — the refusal is what is under test
        return error
    return None


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    clock = [0.0]
    handler = SpendCap(spend_of=spend_of, clock=lambda: clock[0])
    litellm.callbacks = [handler]
    # The proxy registers config callbacks for success events too.
    litellm.logging_callback_manager.add_litellm_async_success_callback(handler)

    def row(name, info):
        return {
            "model_name": name,
            "litellm_params": {
                "model": "gemini/gemini-4-argon",
                "api_key": "k",
                "api_base": base,
                "input_cost_per_token": 0.000004,
                "output_cost_per_token": 0.00002,
            },
            "model_info": info,
        }

    router = Router(
        model_list=[row("capped", {"cheese_spend_cap_usd": 10}), row("uncapped", {})],
        num_retries=0,
    )

    LEDGER["gemini/gemini-4-argon"] = 9.0
    assert await call(router, "capped") is None and CALLS, CALLS
    assert ASKED == ["gemini/gemini-4-argon"], ASKED

    # $9 read + $4 since = $13: refused before the next read, upstream not called.
    await asyncio.sleep(0.2)  # success callbacks run after the response
    refused = await call(router, "capped")
    assert refused is not None and "spend cap" in str(refused), refused
    assert CALLS == [], CALLS

    # A call just before a read still counts after it, since the ledger may
    # not hold it yet. Read $3 at t=61; calls at 61 and 116 ($4 each); the
    # read at 121 still says $3, but the 116 call is within the flush lag:
    # $3 + $4 = $7 lets one more call through, and the next ($11) is refused.
    LEDGER["gemini/gemini-4-argon"] = 3.0
    for t in (61, 116, 121):
        clock[0] = t
        assert await call(router, "capped") is None and CALLS, t
        await asyncio.sleep(0.2)
    clock[0] = 122
    refused = await call(router, "capped")
    assert refused is not None and CALLS == [], refused

    # Once those calls are older than the flush lag, the ledger alone decides.
    LEDGER["gemini/gemini-4-argon"] = 2.0
    clock[0] = 200
    assert await call(router, "capped") is None and CALLS

    LEDGER["gemini/gemini-4-argon"] = 10_000.0
    clock[0] += 61
    assert await call(router, "uncapped") is None and CALLS
    server.shutdown()
    print("spend cap ok")


asyncio.run(main())
