"""A DeepSeek answer that searched costs its tokens plus each search it ran.

Goes through the proxy's own `/v1/messages` route, under the
`deepseek-flash` row of test_rows.yaml, to a local DeepSeek-shaped upstream whose answer reports
how many searches it ran, streamed and not. The cost LiteLLM logs is the spend
every key is charged in credits from, so a search priced wrong here is charged
wrong everywhere. A LiteLLM upgrade that stops pricing searches, prices one
per answer again, or drops the row's price fails here.
"""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import isclose
from pathlib import Path

import httpx
import litellm
import yaml
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy import proxy_server
from litellm.router import Router

os.environ.setdefault("DEEPSEEK_API_KEY", "k")

SEARCHED = 3
SEARCH_BLOCKS = [
    {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}},
    {
        "type": "web_search_tool_result",
        "tool_use_id": "s1",
        "content": [
            {"type": "web_search_result", "url": "https://example.com", "title": "e"}
        ],
    },
]
ANSWER: dict = {}


def reply(searches: int) -> dict:
    usage: dict = {"input_tokens": 1000, "output_tokens": 100}
    content = [{"type": "text", "text": "ok"}]
    if searches:
        usage["server_tool_use"] = {"web_search_requests": searches}
        content = [*SEARCH_BLOCKS, *content]
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "deepseek-flash",
        "content": content,
        "stop_reason": "end_turn",
        "usage": usage,
    }


def events(message: dict) -> bytes:
    start = {**message, "content": [], "usage": {"input_tokens": 1000, "output_tokens": 0}}
    out = [("message_start", {"type": "message_start", "message": start})]
    for i, block in enumerate(message["content"]):
        if block["type"] == "text":
            out.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}))
            out.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": block["text"]}}))
        else:
            out.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": block}))
        out.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    out.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": message["usage"]}))
    out.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in out).encode()


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
        if body.get("stream"):
            data, kind = events(ANSWER["message"]), "text/event-stream"
        else:
            data, kind = json.dumps(ANSWER["message"]).encode(), "application/json"
        self.send_response(200)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


COSTS: list[float] = []


class Costs(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        COSTS.append(kwargs.get("response_cost"))


def official_row(base: str) -> dict:
    """The deepseek-flash row on DeepSeek's own API (test_rows.yaml), pointed here."""
    rows = [
        row
        for row in yaml.safe_load(Path(__file__).with_name("test_rows.yaml").read_text())["model_list"]
        if row["model_name"] == "deepseek-flash"
        and row["litellm_params"].get("api_key") == "os.environ/DEEPSEEK_API_KEY"
    ]
    assert len(rows) == 1, rows
    row = json.loads(json.dumps(rows[0]))
    row["litellm_params"]["api_key"] = "k"
    row["litellm_params"]["api_base"] = f"{base}/anthropic"
    return row


async def cost_of(row: dict, searches: int, stream: bool) -> float:
    ANSWER["message"] = reply(searches)
    proxy_server.llm_router = Router(model_list=[row])
    proxy_server.master_key = None
    litellm.callbacks = [Costs()]
    COSTS.clear()
    transport = httpx.ASGITransport(app=proxy_server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway", timeout=30) as client:
        response = await client.post(
            "/v1/messages",
            json={
                "model": "deepseek-flash",
                "max_tokens": 64,
                "stream": stream,
                "messages": [{"role": "user", "content": "search for q"}],
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
            },
        )
        assert response.status_code == 200, (response.status_code, response.text)
        response.read()
    for _ in range(100):
        if COSTS:
            break
        await asyncio.sleep(0.05)
    assert COSTS, "no cost was logged"
    return COSTS[-1]


async def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    row = official_row(f"http://127.0.0.1:{server.server_port}")
    info = row["model_info"]
    per_search = info["search_context_cost_per_query"]["search_context_size_medium"]
    assert per_search > 0, info
    tokens = 1000 * info["input_cost_per_token"] + 100 * info["output_cost_per_token"]
    for stream in (False, True):
        searched = await cost_of(row, SEARCHED, stream)
        assert isclose(searched, tokens + SEARCHED * per_search, rel_tol=1e-9), (stream, searched)
        plain = await cost_of(row, 0, stream)
        assert isclose(plain, tokens, rel_tol=1e-9), (stream, plain)
    server.shutdown()


asyncio.run(main())
