"""Function-call arguments reach Messages exactly once, with or without Responses deltas.

Both event sequences were recorded from the ChatGPT Codex backend through the dev
gateway (2026-10-12): two parallel read_file calls, once without any
`function_call_arguments.delta` and once with them.
"""

import asyncio
import json

from litellm.llms.anthropic.experimental_pass_through.responses_adapters.streaming_iterator import (
    AnthropicResponsesStreamWrapper,
)


def call(item_id, index, args, deltas):
    item = {"type": "function_call", "id": item_id, "call_id": f"call_{index}", "name": "read_file"}
    events = [{"type": "response.output_item.added", "output_index": index, "item": {**item, "arguments": ""}}]
    if deltas:
        events.append({"type": "response.function_call_arguments.delta", "item_id": item_id, "output_index": index, "delta": args})
    events.append({"type": "response.function_call_arguments.done", "item_id": item_id, "output_index": index, "arguments": args})
    events.append({"type": "response.output_item.done", "output_index": index, "item": {**item, "arguments": args}})
    return events


def stream(deltas, *, done_event=True):
    events = [{"type": "response.created", "response": {"id": "resp_1"}}]
    events += call("fc_a", 0, '{"path": "/a.txt"}', deltas)
    events += call("fc_b", 1, '{"path": "/b.txt"}', deltas)
    if not done_event:
        events = [e for e in events if e["type"] != "response.function_call_arguments.done"]
    events.append({"type": "response.completed", "response": {"id": "resp_1", "status": "completed", "output": [], "usage": None}})
    return events


async def inputs(events):
    chunks = [c async for c in AnthropicResponsesStreamWrapper(iter(events), model="gpt-test")]
    parts = {}
    for c in chunks:
        if c["type"] == "content_block_start":
            assert c["content_block"]["type"] == "tool_use", c
            parts[c["index"]] = []
        elif c["type"] == "content_block_delta":
            assert c["delta"]["type"] == "input_json_delta", c
            parts[c["index"]].append(c["delta"]["partial_json"])
    return {i: (len(p), json.loads("".join(p)) if p else {}) for i, p in parts.items()}


expected = {0: (1, {"path": "/a.txt"}), 1: (1, {"path": "/b.txt"})}
for name, events in (
    ("no deltas", stream(deltas=False)),
    ("deltas", stream(deltas=True)),
    ("no deltas, output_item.done only", stream(deltas=False, done_event=False)),
):
    got = asyncio.run(inputs(events))
    assert got == expected, (name, got)
    print(f"PASS: {name}: both parallel calls carry their arguments exactly once")
