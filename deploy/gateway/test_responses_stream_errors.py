"""A Responses upstream that fails mid-stream must reach a /v1/messages caller as a failure."""

import asyncio
import json

from litellm import Router

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
# A text delta whose `output_item.done` never arrived: the block is still open
# when the stream ends.
DELTA = ANSWER[:-1]
# A function call the upstream never finished.
TOOL_CALL = {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "canary", "arguments": ""}
TOOL_STARTED = {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": TOOL_CALL}


def tool_arguments(partial):
    return {
        "type": "response.function_call_arguments.delta", "sequence_number": 2,
        "item_id": "fc_1", "output_index": 0, "delta": partial,
    }


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
INCOMPLETE = {
    **COMPLETED,
    "type": "response.incomplete",
    "response": {
        **COMPLETED["response"],
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
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

UPSTREAMS = {
    # The connection drops after the stream opened, before any content.
    "dropped": ([CREATED], False),
    # The upstream reports its own failure inside the stream.
    "failed": ([CREATED, FAILED], True),
    # HTTP framing finished, but the model never completed its response.
    "empty": ([], True),
    "clean-eof": ([CREATED], True),
    "partial-eof": ([CREATED, *ANSWER], True),
    # The stream ends while a text block is open (a clean EOF, and a drop).
    "mid-delta-eof": ([CREATED, *DELTA], True),
    "mid-delta-drop": ([CREATED, *DELTA], False),
    # The stream ends while a tool_use block is open (full and partial JSON).
    "tool-full-eof": ([CREATED, TOOL_STARTED, tool_arguments('{"x":"hi"}')], True),
    "tool-partial-eof": ([CREATED, TOOL_STARTED, tool_arguments('{"x":"hi"')], True),
    "complete": ([CREATED, *ANSWER, COMPLETED], True),
    "incomplete": ([CREATED, *ANSWER, INCOMPLETE], True),
}


def frame(event):
    body = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
    return b"%x\r\n%s\r\n" % (len(body), body)


async def serve(reader, writer):
    head = await reader.readuntil(b"\r\n\r\n")
    length = int(next(
        line.split(b":", 1)[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:")
    ))
    body = json.loads(await reader.readexactly(length))
    events, finishes = UPSTREAMS[body["input"][-1]["content"][0]["text"]]
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
    for event in events:
        writer.write(frame(event))
        await writer.drain()
    if finishes:
        writer.write(b"0\r\n\r\n")
        await writer.drain()
    writer.close()


async def call(router, upstream):
    """What the caller observes: the SSE frames it was sent, then how the stream ended."""
    frames = []
    try:
        stream = await router.aanthropic_messages(
            model="gpt-test",
            max_tokens=16,
            stream=True,
            messages=[{"role": "user", "content": [{"type": "text", "text": upstream}]}],
        )
        async for chunk in stream:
            frames.extend(part for part in chunk.decode().split("\n\n") if part.strip())
    except Exception as error:  # noqa: BLE001  # a raised error is one of the two acceptable outcomes
        return frames, error
    return frames, None


def events(frames):
    return [line.split(": ", 1)[1] for f in frames for line in f.splitlines() if line.startswith("event: ")]


def named(frames):
    """Every (event name, data) the caller was sent, in order."""
    out = []
    for block in frames:
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            out.append((fields["event"], json.loads(fields["data"])))
    return out


def text_of(frames):
    return "".join(
        data["delta"]["text"] for name, data in named(frames)
        if name == "content_block_delta" and "text" in data.get("delta", {})
    )


class FailingUpstream:
    """A real, consumed-once async iterator that fails mid-answer, like a dropped upstream."""

    def __init__(self):
        self._events = self._stream()

    async def _stream(self):
        yield CREATED
        yield ANSWER[0]
        yield ANSWER[2]
        raise RuntimeError("connection reset by peer")

    def __aiter__(self):
        return self._events


async def check_adapter_ends_with_one_error():
    """A mid-answer failure reaches the adapter's own consumer as one error, then ends."""
    from litellm.llms.anthropic.experimental_pass_through.responses_adapters.streaming_iterator import (
        AnthropicResponsesStreamWrapper,
    )

    names = []
    async for chunk in AnthropicResponsesStreamWrapper(FailingUpstream(), "gpt-test"):
        names.append(chunk["type"])
        assert len(names) < 20, names  # the iteration must end, not loop on the failure
    assert names.count("error") == 1, names
    assert names[0] == "message_start" and names[-1] == "error", names
    assert "content_block_stop" in names, names


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
    await check_adapter_ends_with_one_error()
    print("PASS: the adapter ends a mid-answer failure with exactly one error", flush=True)
    async with server:
        frames, error = await call(router, "complete")
        assert error is None, error
        assert "message_stop" in events(frames) and "error" not in events(frames), frames
        assert any('"text": "OK"' in f or '"text":"OK"' in f for f in frames), frames
        print("PASS: a completed upstream stream reaches the caller whole", flush=True)
        frames, error = await call(router, "incomplete")
        assert error is None and "error" not in events(frames), (frames, error)
        assert events(frames).count("message_stop") == 1, frames
        assert any('"max_tokens"' in f for f in frames), frames
        print("PASS: a declared token limit keeps its normal stop reason", flush=True)
        for upstream in ("dropped", "failed", "empty", "clean-eof", "partial-eof"):
            frames, error = await call(router, upstream)
            assert "message_stop" not in events(frames), (upstream, frames)
            assert error is not None or "error" in events(frames), (
                f"{upstream}: the caller got a stream that ended cleanly with no content: {frames}"
            )
            assert events(frames).count("error") <= 1, (upstream, frames)
            print(f"PASS: a {upstream} upstream stream reaches the caller as a failure", flush=True)
        for upstream in ("mid-delta-eof", "mid-delta-drop"):
            frames, error = await call(router, upstream)
            names = events(frames)
            assert names.count("error") == 1, (upstream, frames)
            assert names.count("content_block_stop") == 1, (upstream, frames)
            assert names.index("content_block_stop") < names.index("error"), (upstream, names)
            assert "message_stop" not in names, (upstream, frames)
            assert text_of(frames) == "OK", (upstream, frames)
            print(f"PASS: a {upstream} closes the open text block before its one error", flush=True)
        frames, error = await call(router, "partial-eof")
        assert events(frames).count("content_block_stop") == 1, frames
        print("PASS: a block the upstream already closed is not stopped a second time", flush=True)
        for upstream in ("tool-full-eof", "tool-partial-eof"):
            frames, error = await call(router, upstream)
            names = events(frames)
            assert "content_block_stop" not in names, (
                f"{upstream}: an unfinished tool block was closed and would run: {frames}"
            )
            assert names.count("error") == 1 and "message_stop" not in names, (upstream, frames)
            assert any(name == "content_block_delta" and data["delta"].get("partial_json")
                       for name, data in named(frames)), (upstream, frames)
            print(f"PASS: an unfinished {upstream} tool block is left open and fails", flush=True)


asyncio.run(main())
