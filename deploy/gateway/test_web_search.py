"""A web search reaches an upstream that runs it, or the client hears that it did not run.

Builds the router from config.yaml's deepseek-flash rows, pointed at a local
upstream: each RUC key answers the way RUC's vLLM does (the model's text, no
search), DeepSeek's API answers with a server_tool_use block and its results.
Requests go through the router the way the proxy sends Claude Code's, and the
streamed answer through the proxy's iterator hook.
"""

import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import litellm
import litellm.proxy.proxy_server as proxy_server
import yaml
from fastapi import HTTPException

import cheese_web_search as web_search
from cheese_web_search import handler

RUC_BASE = "os.environ/RUC_DEEPSEEK_API_BASE"
SEEN: list[str] = []
STATE = {"official": "search"}


def sse(events):
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events
    )


def message(blocks, usage=None):
    start = {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "model": "m", "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}
    events = [start]
    for i, block in enumerate(blocks):
        events.append({"type": "content_block_start", "index": i, "content_block": block})
        if block["type"] == "text":
            events[-1]["content_block"] = {"type": "text", "text": ""}
            events.append({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": block["text"]}})
        events.append({"type": "content_block_stop", "index": i})
    events.append({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1, **(usage or {})}})
    events.append({"type": "message_stop"})
    return sse(events)


SEARCHED = [
    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "q"}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [{"type": "web_search_result", "title": "A page", "url": "https://example.com/a", "encrypted_content": "x", "page_age": None}]},
    {"type": "text", "text": "found it"},
]
UNSEARCHED = [{"type": "text", "text": "I'll search for that."}]
FAILED = sse([{"type": "error", "error": {"type": "overloaded_error", "message": "upstream busy"}}])


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        who = self.path.split("/")[1]
        SEEN.append(who)
        if who == "official" and STATE["official"] == "error":
            payload = FAILED
        elif who == "official" and body.get("tools"):
            payload = message(SEARCHED, {"server_tool_use": {"web_search_requests": 1}})
        else:
            payload = message(UNSEARCHED if body.get("tools") else [{"type": "text", "text": who}])
        if not body.get("stream"):
            blocks = SEARCHED if (who == "official" and body.get("tools")) else UNSEARCHED
            data = json.dumps({"id": "m", "type": "message", "role": "assistant", "model": "m", "content": blocks, "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
            ctype = "application/json"
        else:
            data, ctype = payload, "text/event-stream"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def router(base, keep=lambda params: True):
    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    rows = []
    for entry in copy.deepcopy(config["model_list"]):
        if entry["model_name"] != "deepseek-flash" or not keep(entry["litellm_params"]):
            continue
        params = entry["litellm_params"]
        name = "k" + params["api_key"].rsplit("_", 1)[1] if params.get("api_base") == RUC_BASE else "official"
        params.update(api_base=f"{base}/{name}", api_key=name)
        rows.append(entry)
    return litellm.Router(model_list=rows, **(config.get("router_settings") or {}))


# What Claude Code 2.1.282's WebSearch sends, as the gateway receives it.
SEARCH = {
    "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
    "tool_choice": {"type": "auto"},
}
OTHER_TOOL = {"name": "Read", "description": "read a file", "input_schema": {"type": "object", "properties": {}}}


async def ask(r, session, stream=True, **extra):
    request = {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "Perform a web search for the query: q"}],
        "max_tokens": 64,
        "stream": stream,
        "metadata": {"session_id": session},
        **extra,
    }
    SEEN.clear()
    response = await r.aanthropic_messages(**request)
    if not stream:
        return SEEN[-1], response, request
    chunks = [c async for c in handler.async_post_call_streaming_iterator_hook(None, response, request)]
    return SEEN[-1], b"".join(web_search._as_bytes(c) for c in chunks), request


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    litellm.callbacks.append(handler)

    r = router(base)
    proxy_server.llm_router = r

    # Every RUC row says it cannot search; DeepSeek's API row does not say so.
    rows = r.get_model_list(model_name="deepseek-flash")
    able = [d["litellm_params"]["api_key"] for d in rows if web_search.executes_web_search(d)]
    assert able == ["official"], able

    # A conversation is on an RUC key; its search goes to DeepSeek's API, and
    # comes back as it was sent, results included.
    who, _, _ = await ask(r, "conversation-1")
    assert who.startswith("k"), who
    who, body, _ = await ask(r, "conversation-1", **SEARCH)
    assert who == "official", who
    assert b'"server_tool_use"' in body and b"https://example.com/a" in body and b"event: error" not in body, body
    # ... and the conversation stays where it was.
    who, _, _ = await ask(r, "conversation-1")
    assert who.startswith("k"), who

    # A request that offers web search among other tools is routed the same
    # way, and its answer is not held to having searched.
    who, body, _ = await ask(r, "conversation-2", tools=[OTHER_TOOL, *SEARCH["tools"]])
    assert who == "official", who

    # A group none of whose deployments can search: the client gets one error
    # event naming the model, and none of the model's text.
    only_ruc = router(base, keep=lambda p: p.get("api_base") == RUC_BASE)
    proxy_server.llm_router = only_ruc
    who, body, _ = await ask(only_ruc, "conversation-3", **SEARCH)
    assert who.startswith("k"), who
    text = body.decode()
    assert text.startswith("event: error\n") and text.count("event:") == 1, text
    error = json.loads(text.split("data: ", 1)[1])
    assert error["error"]["message"] == web_search.not_run("deepseek-flash"), error
    assert "I'll search" not in text, text

    # Without a search to run, the same upstream's answer passes untouched,
    # and so does one to a request that offers search among other tools.
    who, body, _ = await ask(only_ruc, "conversation-4")
    assert b"event: error" not in body and b'"k' in body, body
    who, body, _ = await ask(only_ruc, "conversation-4", tools=[OTHER_TOOL, *SEARCH["tools"]])
    assert b"event: error" not in body and b"I'll search" in body, body

    # Not streamed: the proxy's success hook refuses an answer without a search.
    who, response, request = await ask(only_ruc, "conversation-5", stream=False, **SEARCH)
    try:
        await handler.async_post_call_success_hook(request, None, response)
    except HTTPException as exc:
        assert exc.status_code == 400 and exc.detail == web_search.not_run("deepseek-flash"), exc
    else:
        raise AssertionError("an answer without a search was let through")

    # An upstream's own error reaches the client as it was, not as ours.
    proxy_server.llm_router = r
    STATE["official"] = "error"
    try:
        who, body, _ = await ask(r, "conversation-6", **SEARCH)
        assert b"upstream busy" in body and b"Web search did not run" not in body, body
    except litellm.exceptions.APIError as exc:  # the router may raise it instead
        assert "upstream busy" in str(exc), exc
    STATE["official"] = "search"

    server.shutdown()
    print("PASS: web search goes to an upstream that runs it, or the client is told it did not run")


if __name__ == "__main__":
    asyncio.run(main())
