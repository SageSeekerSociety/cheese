"""An upstream that fails mid-stream must reach a /v1/messages caller as an Anthropic error.

Drives the proxy's own /v1/messages endpoint against a local fake Responses
upstream, with no fallback configured. Claude Code reads only named SSE events:
an error that arrives as anything other than `event: error` is dropped, and the
turn ends with "Stream ended without receiving any events" instead of a retry.
Claude Code retries a streamed error only when its type is `overloaded_error`.
"""

import asyncio
import json
from unittest.mock import patch

import httpx
import litellm
import litellm.proxy.proxy_server as proxy_server
from litellm import Router

# The Responses events a ChatGPT-backed model streams, as in test_responses_stream_errors.py.
CREATED = {
    "type": "response.created",
    "sequence_number": 0,
    "response": {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "status": "in_progress",
        "model": "gpt-test",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    },
}
MESSAGE = {"id": "msg_1", "type": "message", "role": "assistant", "status": "in_progress", "content": []}
TEXT = {"type": "output_text", "text": "OK", "annotations": []}
ANSWER = [
    {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": MESSAGE},
    {
        "type": "response.content_part.added", "sequence_number": 2, "item_id": "msg_1",
        "output_index": 0, "content_index": 0, "part": {**TEXT, "text": ""},
    },
    {
        "type": "response.output_text.delta", "sequence_number": 3, "item_id": "msg_1",
        "output_index": 0, "content_index": 0, "delta": "OK",
    },
    {
        "type": "response.output_item.done", "sequence_number": 4, "output_index": 0,
        "item": {**MESSAGE, "status": "completed", "content": [TEXT]},
    },
]
COMPLETED = {
    "type": "response.completed",
    "sequence_number": 5,
    "response": {
        **CREATED["response"],
        "status": "completed",
        "output": [{**MESSAGE, "status": "completed", "content": [TEXT]}],
        "usage": {
            "input_tokens": 3,
            "output_tokens": 1,
            "total_tokens": 4,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    },
}
FAILED = {
    "type": "response.failed",
    "sequence_number": 1,
    "response": {
        **CREATED["response"],
        "status": "failed",
        "error": {"code": "server_error", "message": "Our servers are currently overloaded."},
    },
}

def frame(event):
    body = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
    return b"%x\r\n%s\r\n" % (len(body), body)


PING_S = 0.2
UPSTREAMS = {
    # The upstream overloads after the gateway has already answered 200 and
    # sent a keepalive ping, the order seen on the dev gateway.
    "late-overload": ([CREATED, PING_S * 4, FAILED], True),
    # The connection drops after the gateway has answered 200.
    "late-drop": ([CREATED, PING_S * 4], False),
    # The upstream closes cleanly after the gateway has answered 200, but never
    # sends a terminal event. A clean EOF is still an incomplete answer.
    "clean-eof": ([CREATED, PING_S * 4], True),
    "partial-eof": ([CREATED, *ANSWER], True),
    # The upstream overloads before the gateway has sent the caller anything.
    "early-overload": ([CREATED, FAILED], True),
    "complete": ([CREATED, *ANSWER, COMPLETED], True),
}


async def serve(reader, writer):
    head = await reader.readuntil(b"\r\n\r\n")
    length = int(next(
        line.split(b":", 1)[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:")
    ))
    body = json.loads(await reader.readexactly(length))
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
    steps, finishes = UPSTREAMS[body["input"][-1]["content"][0]["text"]]
    for step in steps:
        if isinstance(step, float):
            await asyncio.sleep(step)
            continue
        writer.write(frame(step))
        await writer.drain()
    if finishes:
        writer.write(b"0\r\n\r\n")
        await writer.drain()
    writer.close()


async def call(client, upstream):
    """The status and the named SSE events the caller receives, as (event, data) pairs."""
    response = await client.post("/v1/messages", json={
        "model": "gpt-test",
        "max_tokens": 16,
        "stream": True,
        "messages": [{"role": "user", "content": [{"type": "text", "text": upstream}]}],
    })
    events = []
    for block in response.text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return response.status_code, events


async def main():
    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    router = Router(model_list=[{
        "model_name": "gpt-test",
        "litellm_params": {
            "model": "openai/gpt-test",
            "api_base": f"http://127.0.0.1:{port}",
            "api_key": "test",
            "use_in_pass_through": False,
        },
    }], num_retries=0)
    litellm.anthropic_sse_ping_interval_seconds = PING_S
    transport = httpx.ASGITransport(app=proxy_server.app)
    async with server, httpx.AsyncClient(transport=transport, base_url="http://gateway", timeout=30) as client:
        with patch.object(proxy_server, "llm_router", router), patch.object(proxy_server, "master_key", None):
            status, events = await call(client, "complete")
            assert status == 200 and [name for name, _ in events][-1] == "message_stop", (status, events)
            print("PASS: a completed upstream stream reaches the caller whole", flush=True)

            for upstream in ("late-overload", "late-drop", "clean-eof"):
                status, events = await call(client, upstream)
                errors = [data for name, data in events if name == "error"]
                assert status == 200 and events and events[0][0] == "ping", (upstream, status, events)
                assert errors and errors[-1]["type"] == "error", (
                    f"{upstream}: the caller got a 200 stream that ended without an error event: {events}"
                )
                assert errors[-1]["error"]["type"] == "overloaded_error", (upstream, errors)
                assert upstream != "late-overload" or "overloaded" in errors[-1]["error"]["message"], errors
                print(f"PASS: a {upstream} after the stream started reaches the caller as overloaded_error", flush=True)

            status, events = await call(client, "partial-eof")
            assert status == 200, (status, events)
            assert [name for name, _ in events].count("error") == 1, events
            assert events[-1][1]["error"]["type"] == "overloaded_error", events
            assert "message_stop" not in [name for name, _ in events], events
            text = "".join(
                data["delta"].get("text", "")
                for name, data in events if name == "content_block_delta"
            )
            assert text == "OK", events
            print("PASS: a partial answer ends in one error, without replay or a false completion", flush=True)

            status, events = await call(client, "early-overload")
            assert status >= 500, (status, events)
            print(f"PASS: an overload before the stream started reaches the caller as HTTP {status}", flush=True)


asyncio.run(main())
