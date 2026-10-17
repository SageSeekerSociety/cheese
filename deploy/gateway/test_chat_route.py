"""A Claude Code request to an OpenAI-compatible upstream that serves
chat/completions only arrives whole, and the answer comes back as Claude Code
reads it.

Such a row uses the `hosted_vllm/` prefix (docs/manual/dev/gateway.md): an
`openai/` row is translated to the Responses API instead, and a
`custom_openai/` row drops the reasoning effort and prepends the OpenAI SDK's
name to the User-Agent. The request carries what a coding agent's turn carries
— thinking, tools, an earlier tool call, and an image a tool returned — and
the upstream streams reasoning, text and a tool call.
"""

import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litellm
from litellm.router import Router

IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="
SEEN: list[tuple[str, dict]] = []


def chunk(delta, finish=None):
    return {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


STREAM = [
    chunk({"role": "assistant", "reasoning_content": "Look at it first."}),
    chunk({"content": "Reading."}),
    chunk({"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "Read", "arguments": "{\"file_path\":"}}]}),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": " \"b.png\"}"}}]}),
    chunk({}, "tool_calls"),
    {**chunk({}), "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
]


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        SEEN.append((self.path, json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))))
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in STREAM:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


MESSAGES = [
    {"role": "user", "content": "Inspect the screenshot"},
    {"role": "assistant", "content": [
        {"type": "text", "text": "ok"},
        {"type": "tool_use", "id": "read-1", "name": "Read", "input": {"file_path": "a.png"}},
    ]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "read-1", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMAGE}},
    ]}]},
]
TOOLS = [{"name": "Read", "description": "Read a file", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}]


async def events(response):
    text = "".join([chunk.decode() if isinstance(chunk, bytes) else str(chunk) async for chunk in response])
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    router = Router(
        model_list=[
            {
                "model_name": "chat-only",
                "litellm_params": {"model": "hosted_vllm/upstream-id", "api_base": f"{base}/chat-only/v1", "api_key": "k"},
                "model_info": {"supports_reasoning": True},
            }
        ]
    )
    litellm.drop_params = True  # as config.yaml sets it
    for name in ("chat-only",):
        SEEN.clear()
        response = await router.aanthropic_messages(
            model=name,
            max_tokens=4096,
            stream=True,
            messages=copy.deepcopy(MESSAGES),
            tools=copy.deepcopy(TOOLS),
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
        )
        got = await events(response)
        (path, body), = SEEN
        assert path == f"/{name}/v1/chat/completions", (name, path)
        assert body["model"] == "upstream-id", (name, body["model"])
        assert body["tools"][0]["function"]["name"] == "Read", (name, body.get("tools"))
        assert body.get("reasoning_effort") == "high", (name, body.get("reasoning_effort"))
        sent = json.dumps(body["messages"])
        assert IMAGE in sent and "read-1" in sent, (name, sent[:500])

        starts = [e["content_block"] for e in got if e["type"] == "content_block_start"]
        assert [b["type"] for b in starts] == ["thinking", "text", "tool_use"], (name, starts)
        partial = "".join(
            e["delta"]["partial_json"] for e in got
            if e["type"] == "content_block_delta" and e["delta"]["type"] == "input_json_delta"
        )
        assert json.loads(partial) == {"file_path": "b.png"}, (name, partial)
        stop = [e["delta"]["stop_reason"] for e in got if e["type"] == "message_delta"]
        assert stop == ["tool_use"], (name, stop)

    server.shutdown()
    print(
        "PASS: a hosted_vllm/ row gets thinking, tools, tool history and images on "
        "chat/completions, and returns thinking, text and tool calls"
    )


asyncio.run(main())
