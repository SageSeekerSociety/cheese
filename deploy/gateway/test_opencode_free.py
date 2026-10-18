"""The free upstream receives agent requests; callers keep their own protocol."""

import asyncio
import copy
import itertools
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import litellm
import yaml
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

URL = "https://opencode.ai/inference/openai/v1/chat/completions"
TOOLS = [
    {"type": "function", "function": {"name": name, "parameters": {
        "type": "object", "properties": {"file_path": {"type": "string"}},
        "required": ["file_path"],
    }}}
    for name in ("Bash", "Read")
]
BODY = {
    "model": "deepseek-v4.1-flash-free", "stream": False,
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "Read the file"},
         {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
        {"role": "assistant", "tool_calls": [{"id": "old", "type": "function",
         "function": {"name": "Read", "arguments": '{"file_path":"old.txt"}'}}]},
        {"role": "tool", "tool_call_id": "old", "content": "previous result"},
    ],
    "tools": TOOLS, "tool_choice": {"type": "function", "function": {"name": "Read"}},
}


class BodyStream(httpx.AsyncByteStream):
    def __init__(self, tool="read", interrupted=False, incomplete=False, chunks=None):
        self.closed = 0
        self.interrupted = interrupted
        chunks = chunks if chunks is not None else [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "checking"}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
             "id": "call_1", "type": "function", "function": {"name": tool, "arguments": ""}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
             "function": {"arguments": '{"file_path":"new.txt"}'}}]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
             "usage": {"prompt_tokens": 18, "completion_tokens": 9, "total_tokens": 27}},
        ]
        if incomplete:
            chunks = chunks[:-1]
        self.raw = b"".join(
            b"data: " + json.dumps({"id": "chatcmpl-free", "object": "chat.completion.chunk",
                                    "created": 1, "model": "deepseek-v4.1-flash-free", **c}).encode() + b"\r\n\r\n"
            for c in chunks
        ) + b"data: [DONE]\r\n\r\n"

    async def __aiter__(self):
        for offset in range(0, len(self.raw), 7):
            yield self.raw[offset:offset + 7]
            if self.interrupted:
                raise asyncio.CancelledError()

    async def aclose(self):
        self.closed += 1


async def main():
    handler = AsyncHTTPHandler()
    seen = []
    streams = []
    tool = "read"
    interrupted = False
    incomplete = False
    check_agent = True
    check_native_sequence = False

    async def send(request, *, stream):
        payload = json.loads(request.content)
        if check_agent:
            assert stream and payload["stream"] is True, "free upstream requires streaming"
            assert request.headers["user-agent"].startswith("opencode/1.")
            session = request.headers["x-opencode-session"]
            assert re.fullmatch(r"ses_[0-9a-f]{12}[0-9A-Za-z]{14}", session)
            if check_native_sequence:
                # OpenCode resets the sequence for each millisecond. Its free
                # admission rejected a session carrying a process-wide count.
                sequence = (int(session[4:16], 16) ^ ((1 << 48) - 1)) & 0xfff
                assert sequence == 1, "new conversations must retain native identifier sequences"
            assert request.headers["x-opencode-session-id"] == session
            assert re.fullmatch(r"msg_[0-9a-f]{12}[0-9A-Za-z]{14}", request.headers["x-opencode-request"])
            assert request.headers["x-opencode-client"] == "cli"
            functions = {t["function"]["name"]: t["function"] for t in payload["tools"]}
            assert "bash" in functions and "read" in functions
            if payload.get("tool_choice", {}).get("type") == "function":
                assert payload["tool_choice"]["function"]["name"] == "read"
                assert functions["read"]["parameters"] == TOOLS[1]["function"]["parameters"]
                assert payload["messages"][0] == BODY["messages"][0]
                assert payload["messages"][1]["tool_calls"][0]["function"]["name"] == "read"
                assert payload["messages"][2] == BODY["messages"][2]
        seen.append(request)
        body = BodyStream(tool, interrupted, incomplete)
        streams.append(body)
        return httpx.Response(200, request=request, stream=body,
                              headers={"content-type": "text/event-stream", "x-upstream-trace": "trace"})

    async def call(payload=None, *, stream=False, key="key-a", url=URL):
        return await handler.post(url, data=json.dumps(BODY if payload is None else payload),
                                  stream=stream, headers={"Authorization": "Bearer public"},
                                  logging_obj=SimpleNamespace(litellm_call_id="test", litellm_params={
                                      "metadata": {"user_api_key_hash": key}}))

    try:
        with patch.object(handler.client, "send", send):
            response = await call()
            result = response.json()
            message = result["choices"][0]["message"]
            assert message["tool_calls"][0]["function"] == {
                "name": "Read", "arguments": '{"file_path":"new.txt"}'}
            assert message["reasoning_content"] == "checking"
            assert result["usage"]["total_tokens"] == 27
            assert response.headers["x-upstream-trace"] == "trace"
            assert streams[-1].closed == 1
            session = seen[-1].headers["x-opencode-session"]
            request_id = seen[-1].headers["x-opencode-request"]
            print("OpenCode free: nonstream response, tools, reasoning, usage, image and history passed")

            response = await call({**copy.deepcopy(BODY), "stream": True}, stream=True)
            raw = await response.aread()
            await response.aclose()
            assert b'"Read"' in raw and b'new.txt' in raw and b'[DONE]' in raw
            assert seen[-1].headers["x-opencode-session"] == session
            assert seen[-1].headers["x-opencode-request"] != request_id
            assert streams[-1].closed == 1
            await (await call(key="key-b")).aclose()
            assert seen[-1].headers["x-opencode-session"] != session
            assert BODY["tools"][1]["function"]["name"] == "Read"
            print("OpenCode free: streaming, conversation affinity, key isolation and input ownership passed")

            clock = itertools.count(1792353000000)
            check_native_sequence = True
            with patch("litellm.llms.custom_httpx.opencode_free.time", SimpleNamespace(time=lambda: next(clock) / 1000)):
                for index in range(128):
                    await (await call(key=f"new-conversation-{index}")).aclose()
            check_native_sequence = False
            print("OpenCode free: fresh conversations remain admissible after sustained requests")

            response = await call(stream=True)
            await response.aclose()
            assert streams[-1].closed == 1
            interrupted = True
            response = await call(stream=True)
            try:
                await response.aread()
                raise AssertionError("cancellation disappeared")
            except asyncio.CancelledError:
                pass
            finally:
                await response.aclose()
            assert streams[-1].closed == 1
            interrupted = False
            print("OpenCode free: early close and cancellation release the upstream body")

            tool = "bash"
            try:
                await call({"model": BODY["model"], "messages": [{"role": "user", "content": "hello"}]})
                raise AssertionError("an unavailable tool was silently accepted")
            except (httpx.ProtocolError, ValueError) as exc:
                assert "unavailable" in str(exc).lower() and "bash" in str(exc).lower(), str(exc)
            assert streams[-1].closed == 1
            print("OpenCode free: calls to unavailable tools fail explicitly")

            tool = "read"
            incomplete = True
            try:
                await call()
                raise AssertionError("an interrupted answer became a successful response")
            except httpx.ProtocolError as exc:
                assert "incomplete" in str(exc).lower(), str(exc)
            assert streams[-1].closed == 1
            incomplete = False
            print("OpenCode free: an unfinished upstream response cannot become a successful answer")

            check_agent = False
            for url, model in ((URL, "paid-model"),
                               (URL.replace("/inference/", "/inference/go/"), BODY["model"]),
                               (URL.replace("opencode.ai", "other.invalid"), BODY["model"])):
                response = await call({"model": model, "messages": [], "stream": False}, url=url)
                assert json.loads(seen[-1].content)["stream"] is False
                assert "x-opencode-request" not in seen[-1].headers
                await response.aclose()
            print("OpenCode free: paid, Go and unrelated providers are unchanged")
    finally:
        await handler.client.aclose()

    requests = []
    fallback = False
    batch_chunks = None

    async def routed_send(client, request, *, stream=False, **kwargs):
        body = json.loads(request.content)
        requests.append(request)
        if request.url.host == "opencode.ai":
            assert stream and body["stream"] is True
            assert "read" in [t["function"]["name"] for t in body["tools"]]
            if fallback:
                return httpx.Response(400, request=request, json={"error": {
                    "message": "maximum context length exceeded", "type": "invalid_request_error",
                    "code": "context_length_exceeded"}})
            return httpx.Response(200, request=request, stream=BodyStream(chunks=batch_chunks),
                                  headers={"content-type": "text/event-stream"})
        assert "x-opencode-request" not in request.headers
        return httpx.Response(200, request=request, json={
            "id": "official", "object": "chat.completion", "created": 1, "model": "official",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "official"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    official = copy.deepcopy(next(row for row in config["model_list"] if row["model_name"] == "deepseek-flash"))
    official["model_name"] = "free-first"
    official["litellm_params"]["api_key"] = "official-key"
    router = litellm.Router(model_list=[
        {"model_name": "free-first", "litellm_params": {
            "model": "hosted_vllm/deepseek-v4.1-flash-free", "api_base": URL.removesuffix("/chat/completions"),
            "api_key": "public", "order": 1}, "model_info": {"supports_reasoning": True}},
        official,
    ], num_retries=2)
    litellm.drop_params = True
    with patch.object(httpx.AsyncClient, "send", routed_send):
        response = await router.aanthropic_messages(
            model="free-first", max_tokens=128000, stream=True,
            messages=[{"role": "user", "content": "Read a file"}],
            tools=[{"name": "Read", "description": "Read a file", "input_schema": TOOLS[1]["function"]["parameters"]}],
            thinking={"type": "adaptive"}, output_config={"effort": "high"},
        )
        raw = "".join([c.decode() if isinstance(c, bytes) else str(c) async for c in response])
        events = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:")]
        calls = [event["content_block"] for event in events if event["type"] == "content_block_start"
                 and event["content_block"]["type"] == "tool_use"]
        assert len(calls) == 1 and calls[0]["name"] == "Read", calls
        assert json.loads("".join(event["delta"]["partial_json"] for event in events
                          if event["type"] == "content_block_delta" and event["delta"]["type"] == "input_json_delta")) == {"file_path": "new.txt"}
        assert json.loads(requests[-1].content)["reasoning_effort"] == "high"
        print("OpenCode free: Claude Code Messages translation preserves effort and real tool calls")

        batch_chunks = [
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "call_a", "type": "function", "function": {
                    "name": "read", "arguments": '{"file_path":"a.txt"}'}},
                {"index": 1, "id": "call_b", "type": "function", "function": {
                    "name": "read", "arguments": '{"file_path":"b'}},
            ]}}]},
            {"choices": [{"index": 0, "finish_reason": "tool_calls", "delta": {"tool_calls": [
                {"index": 1, "function": {"arguments": '.txt"}'}},
                {"index": 2, "id": "call_c", "type": "function", "function": {
                    "name": "read", "arguments": '{"file_path":"c.txt"}'}},
            ]}}], "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50}},
        ]
        response = await router.aanthropic_messages(
            model="free-first", max_tokens=4096, stream=True,
            messages=[{"role": "user", "content": "Read a.txt, b.txt and c.txt in parallel"}],
            tools=[{"name": "Read", "description": "Read a file", "input_schema": TOOLS[1]["function"]["parameters"]}],
        )
        raw = "".join([c.decode() if isinstance(c, bytes) else str(c) async for c in response])
        events = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:")]
        calls = {event["index"]: event["content_block"] for event in events
                 if event["type"] == "content_block_start" and event["content_block"]["type"] == "tool_use"}
        assert len(calls) == 3 and {call["id"] for call in calls.values()} == {"call_a", "call_b", "call_c"}, calls
        arguments = {}
        for event in events:
            if event["type"] == "content_block_delta" and event["delta"]["type"] == "input_json_delta":
                index = event["index"]
                arguments[index] = arguments.get(index, "") + event["delta"]["partial_json"]
        assert {json.loads(value)["file_path"] for value in arguments.values()} == {"a.txt", "b.txt", "c.txt"}
        assert all(call["name"] == "Read" for call in calls.values())
        batch_chunks = None
        print("OpenCode free: batched parallel tool calls retain separate ids and valid arguments")

        fallback = True
        requests.clear()
        response = await router.acompletion(model="free-first", messages=[{"role": "user", "content": "long context"}])
        assert response.choices[0].message.content == "official", response
        assert requests[0].url.host == "opencode.ai" and requests[-1].url.host == "api.deepseek.com"
        print("OpenCode free: upstream context overflow falls through to the existing official source")


if __name__ == "__main__":
    asyncio.run(main())
